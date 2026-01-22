import os
import re
import logging
import time
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURACIÓN Y SECRETOS ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

# Ajuste para Heroku/Render/Vercel que usan 'postgres://'
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    logging.error("Falta DATABASE_URL")
    exit()

# --- BASE DE DATOS ---
engine = create_engine(DATABASE_URL)
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

# --- CLIENTES API ---
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)

def limpiar_titulo(texto):
    texto_limpio = re.sub(r'[^\w\s\u00C0-\u00FF.,!¡?¿\-:;"\']', '', texto)
    return re.sub(r'\s+', ' ', texto_limpio).strip()

def get_latest_videos(channel_id):
    try:
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        response = request.execute()
        return [{
            'id': i['id']['videoId'], 
            'title': i['snippet']['title'],
            'desc': i['snippet']['description'] # Guardamos descripción para emergencia
        } for i in response.get('items', [])]
    except Exception as e:
        logging.error(f"Error API Youtube Data: {e}")
        return []

# --- LÓGICA DE EXTRACCIÓN ROBUSTA (CASCADA) ---
def obtener_contenido_inteligente(video_id, descripcion_backup):
    """
    Intenta obtener la transcripción por todos los medios.
    Si falla, devuelve la descripción.
    """
    print(f"DEBUG: 🕵️ Analizando video {video_id}...")
    
    transcript_text = ""

    # METODO 1: COOKIES (El más efectivo para evitar bloqueos)
    if os.path.exists('cookies.txt'):
        print("   🍪 Intentando con cookies.txt...")
        try:
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id, 
                languages=['es', 'es-419', 'en'], 
                cookies='cookies.txt'
            )
            transcript_text = " ".join([entry['text'] for entry in transcript])
            print("   ✅ ÉXITO: Transcripción obtenida con Cookies.")
            return transcript_text, "FULL"
        except Exception as e:
            print(f"   ⚠️ Fallo con cookies: {e}")
    else:
        print("   ℹ️ No se encontró cookies.txt (Saltando Método 1)")

    # METODO 2: DIRECTO (Sin cookies, modo invitado)
    print("   🌐 Intentando modo directo (Guest)...")
    try:
        transcript = YouTubeTranscriptApi.get_transcript(
            video_id, 
            languages=['es', 'es-419', 'en']
        )
        transcript_text = " ".join([entry['text'] for entry in transcript])
        print("   ✅ ÉXITO: Transcripción obtenida directa.")
        return transcript_text, "FULL"
    except Exception as e:
        print(f"   ⚠️ Fallo directo: {e}")

    # METODO 3: ULTIMO RECURSO (Descripción)
    print("   🛡️ Activando protocolo de emergencia: Usando Descripción.")
    if descripcion_backup and len(descripcion_backup) > 50:
        return descripcion_backup, "RESUMEN"
    
    print("   ❌ FALLO TOTAL: No hay transcripción ni descripción válida.")
    return None, None

# --- GENERACIÓN DE NOTICIA ---
def generate_news(text, title, modo):
    if not text: return None
    
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    if modo == "FULL":
        prompt = f"""
        Actúa como un periodista experto en tecnología.
        Escribe una noticia completa en formato HTML para WordPress basada en la siguiente transcripción de video.
        
        TITULO: {title}
        TRANSCRIPCIÓN: {text[:25000]}
        
        REGLAS:
        1. Usa etiquetas HTML: <h2> para subtítulos, <p> para párrafos, <ul> para listas.
        2. Tono: Profesional, informativo y objetivo.
        3. Longitud: Mínimo 350 palabras.
        4. No inventes datos que no estén en el texto.
        """
    else: # Modo RESUMEN
        prompt = f"""
        Actúa como un periodista. Tenemos información limitada de este video.
        Escribe una noticia BREVE en HTML basada en la descripción disponible.
        
        TITULO: {title}
        DESCRIPCIÓN DEL VIDEO: {text}
        
        REGLAS:
        1. Infiere el contexto del video.
        2. Aclara que "El video trata sobre..." o "Se discute...".
        3. Usa HTML simple (<h2>, <p>).
        4. Sé conciso pero profesional.
        """

    try:
        return model.generate_content(prompt).text
    except Exception as e:
        logging.error(f"Error Gemini: {e}")
        return None

# --- BUCLE PRINCIPAL ---
def main():
    session = Session()
    try:
        cid = CHANNEL_ID
        if not cid: return

        logging.info("--- INICIANDO CICLO DE BOT ---")
        videos = get_latest_videos(cid)
        logging.info(f"Encontrados {len(videos)} videos recientes.")

        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            vdesc = v['desc']

            # Verificar si ya existe en BD
            if session.query(VideoNoticia).filter_by(id=vid).first():
                logging.info(f"⏭️  [SALTADO] Ya existe: {vtitle}")
                continue

            logging.info(f"✨ [NUEVO] Procesando: {vtitle}")
            
            # Obtener contenido (Cascada: Cookies -> Directo -> Descripción)
            texto_contenido, modo = obtener_contenido_inteligente(vid, vdesc)
            
            if texto_contenido:
                html = generate_news(texto_contenido, vtitle, modo)
                if html:
                    post = VideoNoticia(
                        id=vid, 
                        titulo=vtitle, 
                        contenido_noticia=html, 
                        url_video=f"https://youtu.be/{vid}"
                    )
                    session.add(post)
                    session.commit()
                    logging.info(f"✅ [GUARDADO] Noticia generada ({modo})")
                else:
                    logging.error("Error generando HTML con Gemini")
            else:
                logging.warning("No se pudo obtener contenido suficiente para la noticia.")

    except Exception as e:
        logging.error(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()