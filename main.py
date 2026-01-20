<<<<<<< HEAD
from youtube_transcript_api import YouTubeTranscriptApi
import sys

def ejecutar_rapido():
    # Puedes cambiar el ID aquí
    video_id = "iMgYVJpQQv8"
    
    try:
        # Modo directo: Sin instancia previa para ahorrar tiempo
        # Traemos el transcript de forma inmediata
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        
        # Generamos el texto
        texto = " ".join([i['text'] for i in data])
        
        # Nombre de archivo simplificado
        with open(f"resultado.txt", "w", encoding="utf-8") as f:
            f.write(texto)
        
        print(f"Done: {video_id}")
    except Exception as e:
        print(f"Fail: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_rapido()
=======
import os
import re
import logging
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# --- CONFIG ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)

def limpiar_titulo(texto):
    texto_limpio = re.sub(r'[^\w\s\u00C0-\u00FF.,!¡?¿\-:;"\']', '', texto)
    return re.sub(r'\s+', ' ', texto_limpio).strip()

def get_latest_videos(channel_id):
    request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
    response = request.execute()
    return [{
        'id': i['id']['videoId'], 
        'title': i['snippet']['title'],
        'desc': i['snippet']['description']
    } for i in response.get('items', [])]

def obtener_contenido(video_id, descripcion):
    print(f"DEBUG: Procesando {video_id}...")
    texto_final = ""
    
    # INTENTO 1: Subtítulos (Transcript)
    try:
        # Busca subtítulos en español o inglés (manuales o auto-generados)
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'es-419', 'en'])
        texto_final = " ".join([entry['text'] for entry in transcript])
        print("   ✅ Subtítulos obtenidos.")
        return texto_final, "TRANSCRIPCION"
    except Exception:
        print("   ⚠️ Sin subtítulos o bloqueado.")

    # INTENTO 2: Descripción (Fallback)
    if descripcion and len(descripcion) > 50:
        print("   ✅ Usando descripción del video.")
        return descripcion, "RESUMEN"
    
    return None, None

def generate_news(text, title, tipo):
    model = genai.GenerativeModel('gemini-1.5-flash')
    if tipo == "TRANSCRIPCION":
        prompt = f"Eres periodista. Crea noticia HTML (+300 palabras) sobre '{title}' usando esta transcripción: {text[:25000]}"
    else:
        prompt = f"Eres periodista. Crea una noticia BREVE basada en esta descripción de video: '{title}'. Info: {text}"
        
    try:
        return model.generate_content(prompt).text
    except: return None

def main():
    session = Session()
    try:
        if not CHANNEL_ID: return
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"[YA EXISTE] {vtitle}")
                continue

            print(f"[NUEVO] {vtitle}")
            texto, tipo = obtener_contenido(vid, v['desc'])
            
            if texto:
                html = generate_news(texto, vtitle, tipo)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print(f" -- ¡GUARDADO! ({tipo})")
            else:
                print(" -- Saltando (Sin datos suficientes)")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()
>>>>>>> 79c213dd797c57e515f1497602fbef55bf89f125
