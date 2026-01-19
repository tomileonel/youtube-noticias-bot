import os
import logging
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# Configuración
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# --- BASE DE DATOS ---
db_string = os.getenv('DATABASE_URL')
if db_string and db_string.startswith("postgres://"):
    db_string = db_string.replace("postgres://", "postgresql://", 1)

if not db_string:
    raise ValueError("Falta DATABASE_URL")

engine = create_engine(db_string)
Base = declarative_base()

class VideoNoticia(Base):
    __tablename__ = 'noticias_youtube'
    id = Column(String, primary_key=True)
    titulo = Column(String)
    contenido_noticia = Column(Text)
    url_video = Column(String)
    fecha_proceso = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- APIS ---
youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

def get_latest_videos(channel_id):
    """Obtiene los últimos 5 videos para asegurar que no se nos pasa ninguno"""
    try:
        # CAMBIO: maxResults=5
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        response = request.execute()
        
        videos = []
        if response.get('items'):
            for item in response['items']:
                videos.append({
                    'id': item['id']['videoId'],
                    'title': item['snippet']['title']
                })
        return videos
    except Exception as e:
        logger.error(f"Error YouTube: {e}")
        return []

def get_transcript(video_id):
    """
    Versión DEPURADA: Intenta forzar la obtención de subtítulos 
    e imprime el error real si falla.
    """
    print(f"DEBUG: Intentando sacar subtítulos para {video_id}...")
    try:
        # 1. Obtenemos el objeto que gestiona los subtítulos
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # DEBUG: Ver qué idiomas detectó la API
        print(f"DEBUG: Idiomas detectados: {[t.language_code for t in transcript_list]}")

        # 2. Estrategia de Selección:
        # Intentamos encontrar uno preferido, si no, agarramos el primero que venga.
        transcript = None
        
        try:
            # Busca prioridad: Español, Latino, Inglés (Manuales o Generados)
            transcript = transcript_list.find_transcript(['es', 'es-419', 'en'])
            print(f"DEBUG: Encontrado subtítulo preferido: {transcript.language_code}")
        except:
            # Si no hay preferidos, iteramos y agarramos el primero disponible (Forzado)
            print("DEBUG: No hay español/inglés. Buscando CUALQUIER otro...")
            for t in transcript_list:
                transcript = t
                break # Agarramos el primero y salimos
        
        if not transcript:
            print("DEBUG: La lista de transcripciones estaba vacía.")
            return None

        # 3. Descargar texto
        fetched = transcript.fetch()
        full_text = " ".join([i['text'] for i in fetched])
        return full_text

    except Exception as e:
        # AQUÍ ESTÁ LA CLAVE: Imprimimos el error real
        print(f" ERROR FATAL EN SUBTITULOS: {str(e)}")
        # Si el error es "Cookies required", es que YouTube bloqueó la IP de GitHub.
        return None

def generate_news(text, title):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Actúa como periodista experto. Crea una noticia HTML para WordPress basada en este video: '{title}'.
    TRANSCRIPCION: {text[:30000]}
    REGLAS: 
    1. Usa etiquetas HTML: <h2>, <p>, <ul>, <strong>.
    2. Tono profesional e informativo.
    3. Mínimo 300 palabras.
    4. NO pongas el título H1, empieza con el contenido.
    """
    try:
        return model.generate_content(prompt).text
    except Exception as e:
        logger.error(f"Error Gemini: {e}")
        return None

def main():
    session = Session()
    try:
        cid = os.getenv('CHANNEL_ID')
        if not cid:
            print("Error: No hay CHANNEL_ID configurado.")
            return

        # 1. Obtener lista de videos recientes
        lista_videos = get_latest_videos(cid)
        
        if not lista_videos: 
            print("No se encontraron videos recientes.")
            return

        print(f"Revisando los últimos {len(lista_videos)} videos...")

        # 2. Iterar sobre cada video (Bucle)
        nuevos_procesados = 0
        
        for video in lista_videos:
            vid = video['id']
            vtitle = video['title']

            # Verificación rápida: ¿Ya existe en DB?
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"[SALTADO] Ya existe: {vtitle}")
                continue # Pasa al siguiente video del bucle
            
            # Si llegamos aquí, es un video NUEVO
            print(f"[PROCESANDO] Nuevo hallazgo: {vtitle}")
            
            text = get_transcript(vid)
            if not text:
                print(f" -- Sin subtítulos. Saltando.")
                continue
            
            html = generate_news(text, vtitle)
            if not html:
                print(" -- Error generando noticia con IA.")
                continue
            
            # Guardar en DB
            post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
            session.add(post)
            session.commit()
            print(f" -- ¡Guardado en Base de Datos!")
            nuevos_procesados += 1

        print(f"--- Fin del ciclo. Se procesaron {nuevos_procesados} noticias nuevas ---")
            
    except Exception as e:
        session.rollback()
        print(f"Error Crítico en main: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()