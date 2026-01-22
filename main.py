import os
import re
import logging
import requests
import json
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- CONFIG ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL: exit()

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
        print(f"Error API Youtube: {e}")
        return []

# --- NIVEL 1: API OFICIAL (Puede fallar en Cloud) ---
def get_transcript_official(video_id):
    print("   🔹 Intento 1: API Oficial...")
    try:
        # Si subiste cookies.txt, úsalo. Si no, intenta directo.
        cookies = 'cookies.txt' if os.path.exists('cookies.txt') else None
        
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, cookies=cookies)
        
        # Buscar español o inglés (o autogenerado traducido)
        try:
            transcript = transcript_list.find_transcript(['es', 'es-419', 'en'])
        except:
            transcript = transcript_list.find_generated_transcript(['en', 'es']).translate('es')
            
        text_data = transcript.fetch()
        full_text = " ".join([t['text'] for t in text_data])
        return full_text
    except Exception as e:
        print(f"      Fallo API Oficial: {e}")
        return None

# --- NIVEL 2: RED INVIDIOUS (Espejos) ---
def get_transcript_invidious(video_id):
    print("   🔹 Intento 2: Red Invidious (Espejos)...")
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.jing.rocks",
        "https://vid.uff.ink",
        "https://yt.artemislena.eu",
        "https://invidious.projectsegfau.lt"
    ]
    
    for base_url in instances:
        try:
            # 1. Obtener metadatos del video
            url = f"{base_url}/api/v1/videos/{video_id}"
            res = requests.get(url, timeout=5)
            if res.status_code != 200: continue
            
            data = res.json()
            captions = data.get('captions', [])
            
            # 2. Buscar subtítulo en español
            selected_caption = None
            for cap in captions:
                if 'es' in cap['label'].lower() or 'spanish' in cap['label'].lower() or cap['lang'] == 'es':
                    selected_caption = cap
                    break
            
            # Si no hay español, probar inglés
            if not selected_caption and captions:
                selected_caption = captions[0]

            if selected_caption:
                # 3. Descargar el texto (suele venir en formato VTT)
                cap_url = f"{base_url}{selected_caption['url']}"
                cap_res = requests.get(cap_url)
                
                # Limpiar el formato VTT para dejar solo texto
                raw_text = cap_res.text
                clean_text = clean_vtt(raw_text)
                if len(clean_text) > 50:
                    print(f"      ✅ Éxito en {base_url}")
                    return clean_text

        except Exception:
            continue
            
    return None

def clean_vtt(vtt_content):
    # Elimina tiempos y cabeceras del formato WebVTT
    lines = vtt_content.splitlines()
    text_lines = []
    for line in lines:
        if '-->' in line or line.strip() == 'WEBVTT' or not line.strip():
            continue
        # Eliminar etiquetas HTML como <c.colorE5E5E5>
        clean_line = re.sub(r'<[^>]+>', '', line)
        text_lines.append(clean_line)
    return " ".join(dict.fromkeys(text_lines)) # Elimina duplicados seguidos y une

# --- NIVEL 3: RED PIPED (Otra alternativa) ---
def get_transcript_piped(video_id):
    print("   🔹 Intento 3: Red Piped...")
    api_url = f"https://pipedapi.kavin.rocks/streams/{video_id}"
    try:
        res = requests.get(api_url, timeout=10)
        data = res.json()
        subtitles = data.get('subtitles', [])
        
        tgt_sub = None
        for sub in subtitles:
            if sub['code'] == 'es':
                tgt_sub = sub
                break
        
        if not tgt_sub and subtitles: tgt_sub = subtitles[0] # Fallback cualquiera

        if tgt_sub:
            # Piped devuelve formato XML/VTT a veces, tratamos de limpiarlo
            sub_res = requests.get(tgt_sub['url'])
            # Usamos BeautifulSoup para extraer solo texto si es XML
            soup = BeautifulSoup(sub_res.text, "html.parser")
            return soup.get_text().replace('\n', ' ')
            
    except Exception as e:
        print(f"      Fallo Piped: {e}")
        return None
    return None

# --- CONTROLADOR PRINCIPAL ---
def obtener_transcripcion_blindada(video_id):
    # Secuencia de intentos
    texto = get_transcript_official(video_id)
    if texto: return texto
    
    texto = get_transcript_invidious(video_id)
    if texto: return texto
    
    texto = get_transcript_piped(video_id)
    if texto: return texto
    
    return None

def generate_news(text, title):
    # Usamos modelo Pro o Flash según disponibilidad
    modelos = ['gemini-1.5-flash', 'gemini-pro']
    for m in modelos:
        try:
            model = genai.GenerativeModel(m)
            prompt = f"Eres periodista. Escribe noticia HTML (+300 palabras) sobre '{title}'. TRANSCRIPCION: {text[:25000]}"
            return model.generate_content(prompt).text
        except: continue
    return None

def main():
    session = Session()
    try:
        if not CHANNEL_ID: return
        print("--- INICIANDO BÚSQUEDA ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⏭️  {vtitle}")
                continue

            print(f"✨ Procesando: {vtitle}")
            
            # AQUÍ OBTENEMOS LA TRANSCRIPCIÓN SÍ O SÍ
            transcript = obtener_transcripcion_blindada(vid)
            
            if transcript:
                print(f"   📜 Transcripción conseguida ({len(transcript)} caracteres)")
                html = generate_news(transcript, vtitle)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO")
                else:
                    print("   ❌ Error en generación IA")
            else:
                print("   ❌ IMPOSIBLE OBTENER TRANSCRIPCIÓN (Se omitirá el video)")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()