import os
import re
import logging
import requests
import json
import google.generativeai as genai
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- CONFIGURACIÓN ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
SUPADATA_KEYS_RAW = os.getenv('SUPADATA_API_KEY', '')
SUPADATA_KEYS = [k.strip() for k in SUPADATA_KEYS_RAW.split(',') if k.strip()]

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL: 
    logging.error("Falta DATABASE_URL")
    exit()

if not SUPADATA_KEYS:
    logging.error("❌ CRÍTICO: No se encontraron tokens en SUPADATA_API_KEY.")
    exit()

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
    try:
        req = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        res = req.execute()
        return [{'id': i['id']['videoId'], 'title': i['snippet']['title']} for i in res.get('items', [])]
    except Exception as e:
        logging.error(f"Error API Youtube: {e}")
        return []

def get_transcript_supadata(video_id):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"   🔹 Solicitando a Supadata ({len(SUPADATA_KEYS)} tokens)...")
    api_url = "https://api.supadata.ai/v1/transcript"
    
    for index, api_key in enumerate(SUPADATA_KEYS):
        headers = {'x-api-key': api_key}
        params = {'url': video_url, 'lang': 'es', 'text': 'true'}
        try:
            response = requests.get(api_url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        text = data.get('content') or data.get('text') or data.get('transcript')
                    else: text = None
                    if text: return text
                except json.JSONDecodeError:
                    if response.text and len(response.text) > 50: return response.text
            elif response.status_code == 402:
                print(f"      💲 Token #{index + 1} agotado. Rotando...")
                continue
        except: continue
    return None

def generate_news(text, title):
    # OPTIMIZACIÓN DE TOKENS GEMINI
    # 1. Si el texto es muy corto (menos de 500 letras), no gastamos IA.
    if not text or len(text) < 500:
        print("      ⚠️ Texto demasiado corto/irrelevante. Ahorrando llamada a Gemini.")
        return None
    
    # 2. Recortamos el texto a 15,000 caracteres (aprox 3500 tokens).
    # Esto evita gastar de más en videos largos de 1 hora.
    texto_optimizado = text[:15000]

    try:
        # Usamos el modelo que confirmaste que funciona
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"Eres periodista. Escribe noticia HTML (+300 palabras) sobre '{title}'. TRANSCRIPCION: {texto_optimizado}"
        return model.generate_content(prompt).text
    except Exception as e:
        print(f"      ❌ Error Gemini: {e}")
        return None

def main():
    session = Session()
    try:
        if not CHANNEL_ID: return
        print("--- INICIANDO PROCESO OPTIMIZADO ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            # --- [ESCUDO DE AHORRO] ---
            # Verificamos PRIMERO la base de datos.
            # Si existe, saltamos TODO lo demás. Costo = 0.
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⛔ [AHORRO] Ya procesado: {vtitle}")
                continue # <--- Esto evita gastar Supadata y Gemini

            # Si pasa el escudo, es un video nuevo real
            print(f"✨ [NUEVO] Procesando: {vtitle}")
            
            # Gasto 1: Supadata
            transcript = get_transcript_supadata(vid)
            
            if transcript:
                print(f"   📜 Transcripción OK.")
                
                # Gasto 2: Gemini (Optimizado)
                html = generate_news(transcript, vtitle)
                
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO EN BD")
                else:
                    print("   ❌ Error generando noticia")
            else:
                print("   ❌ OMITIDO (Fallo Supadata)")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()