import os
import re
import logging
import requests
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- CONFIGURACIÓN ---
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

# --- NIVEL 1: API OFICIAL (Sintaxis basada en Instancia) ---
def get_transcript_official(video_id):
    print("   🔹 Intento 1: API Oficial (Sintaxis Instancia)...")
    try:
        # 1. Instanciar la clase (Como pide tu documentación)
        ytt_api = YouTubeTranscriptApi()
        
        # 2. Obtener la lista de transcripciones disponibles usando .list()
        transcript_list = ytt_api.list(video_id)
        
        transcript = None
        
        # 3. Buscar manual o generado en Español o Inglés
        # Intentamos buscar español directamente
        try:
            transcript = transcript_list.find_transcript(['es', 'es-419'])
            print("      ✅ Encontrado subtítulo nativo en Español.")
        except:
            # Si falla, buscamos inglés
            try:
                transcript = transcript_list.find_transcript(['en'])
                print("      ⚠️ Encontrado inglés, se traducirá...")
            except:
                # Si falla, buscamos cualquiera generado automáticamente
                try:
                    transcript = transcript_list.find_generated_transcript(['es', 'en'])
                except:
                    print("      ⚠️ No se encontró nativo/generado específico.")
        
        if not transcript:
            # Último intento: agarrar el primero que haya y traducirlo
            try:
                for t in transcript_list:
                    transcript = t
                    break
            except:
                return None

        # 4. Si no es español, traducir
        if transcript and transcript.language_code not in ['es', 'es-419']:
            try:
                if transcript.is_translatable:
                    transcript = transcript.translate('es')
                    print("      ✅ Traducido a Español.")
            except Exception as e:
                print(f"      ⚠️ No se pudo traducir: {e}")

        # 5. Descargar ("fetch")
        text_data = transcript.fetch()
        full_text = " ".join([t['text'] for t in text_data])
        return full_text

    except Exception as e:
        print(f"      ❌ Fallo API Oficial: {e}")
        return None

# --- NIVEL 2: RED INVIDIOUS (Respaldo) ---
def get_transcript_invidious(video_id):
    print("   🔹 Intento 2: Red Invidious (Espejos)...")
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.jing.rocks",
        "https://vid.uff.ink",
        "https://yt.artemislena.eu"
    ]
    
    for base_url in instances:
        try:
            url = f"{base_url}/api/v1/videos/{video_id}"
            res = requests.get(url, timeout=5)
            if res.status_code != 200: continue
            
            data = res.json()
            captions = data.get('captions', [])
            
            selected = None
            for cap in captions:
                if 'es' in cap['label'].lower():
                    selected = cap
                    break
            
            if not selected and captions: selected = captions[0]

            if selected:
                cap_res = requests.get(f"{base_url}{selected['url']}")
                return clean_vtt(cap_res.text)
        except: continue
    return None

def clean_vtt(vtt_content):
    lines = vtt_content.splitlines()
    text = []
    for line in lines:
        if '-->' not in line and 'WEBVTT' not in line and line.strip():
            text.append(re.sub(r'<[^>]+>', '', line))
    return " ".join(dict.fromkeys(text))

# --- CONTROLADOR ---
def obtener_transcripcion_blindada(video_id):
    texto = get_transcript_official(video_id)
    if texto: return texto
    
    texto = get_transcript_invidious(video_id)
    if texto: return texto
    
    return None

def generate_news(text, title):
    # Usamos gemini-pro que es más estable en versiones viejas de la lib
    modelos = ['gemini-pro', 'gemini-1.5-flash']
    
    for m in modelos:
        try:
            model = genai.GenerativeModel(m)
            prompt = f"Eres periodista. Escribe noticia HTML (+300 palabras) sobre '{title}'. TRANSCRIPCION: {text[:25000]}"
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"   ⚠️ Error modelo {m}: {e}")
            continue
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
            transcript = obtener_transcripcion_blindada(vid)
            
            if transcript:
                print(f"   📜 Transcripción OK.")
                html = generate_news(transcript, vtitle)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO")
                else:
                    print("   ❌ Error Gemini")
            else:
                print("   ❌ IMPOSIBLE OBTENER TRANSCRIPCIÓN")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()