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

# Configuración de Logs más detallada
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURACIÓN ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

# Procesamiento de tokens Supadata (separados por coma)
SUPADATA_KEYS_RAW = os.getenv('SUPADATA_API_KEY', '')
SUPADATA_KEYS = [k.strip() for k in SUPADATA_KEYS_RAW.split(',') if k.strip()]

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL: 
    logging.error("Falta DATABASE_URL")
    exit()

if not SUPADATA_KEYS:
    logging.error("❌ CRÍTICO: No se encontraron tokens en SUPADATA_API_KEY.")
    logging.error("Asegúrate de separar los tokens por comas en los Secretos de GitHub.")
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

# --- LÓGICA SUPADATA CORREGIDA ---
def get_transcript_supadata(video_id):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"   🔹 Solicitando a Supadata ({len(SUPADATA_KEYS)} tokens disponibles)...")
    
    # Endpoint actualizado: Usamos 'transcript' genérico con URL y forzamos español
    api_url = "https://api.supadata.ai/v1/transcript"
    
    for index, api_key in enumerate(SUPADATA_KEYS):
        # Enmascaramos el token para el log
        token_mask = api_key[:4] + "..." + api_key[-4:]
        print(f"      🔑 Probando Token #{index + 1} ({token_mask})")
        
        headers = {'x-api-key': api_key}
        # Parametros clave: URL completa, lang=es, text=true
        params = {
            'url': video_url,
            'lang': 'es',
            'text': 'true'
        }

        try:
            response = requests.get(api_url, headers=headers, params=params, timeout=30)
            
            # --- DIAGNÓSTICO DE RESPUESTA ---
            if response.status_code == 200:
                # Intentamos parsear JSON primero
                try:
                    data = response.json()
                    # A veces devuelve {'content': 'texto...'} o {'results': ...}
                    if isinstance(data, dict):
                        text = data.get('content') or data.get('text') or data.get('transcript')
                    else:
                        text = None # Formato desconocido
                        
                    if text:
                        print(f"      ✅ ÉXITO (JSON válido) con Token #{index + 1}")
                        return text
                except json.JSONDecodeError:
                    # Si falla el JSON, es probable que haya devuelto TEXTO PLANO directamente
                    if response.text and len(response.text) > 50:
                        print(f"      ✅ ÉXITO (Texto Plano) con Token #{index + 1}")
                        return response.text
                
                print("      ⚠️ Respuesta 200 OK pero sin contenido reconocible.")
                print(f"      DEBUG RAW: {response.text[:200]}...") # Imprime el inicio para ver qué devolvió

            elif response.status_code in [401, 403]:
                print(f"      ⛔ Token inválido o sin permisos (Error {response.status_code}). Rotando...")
            elif response.status_code == 402:
                print(f"      💲 Token sin saldo (Error {response.status_code}). Rotando...")
            elif response.status_code == 429:
                print(f"      ⏳ Rate Limit excedido (Error {response.status_code}). Rotando...")
            elif response.status_code == 500:
                print(f"      🔥 Error Interno de Supadata (500). El video podría no tener subtítulos.")
                # Si es error 500, a veces es culpa del video, no del token. Pero seguimos probando.
            else:
                print(f"      ❌ Error desconocido: {response.status_code} - {response.text}")

        except Exception as e:
            print(f"      ❌ Error de conexión: {e}")
            continue

    print("   ❌ FALLO TOTAL: Ningún token pudo obtener la transcripción.")
    return None

def generate_news(text, title):
    if not text or len(text) < 100: return None
    
    # Probamos modelos por orden de estabilidad
    modelos = ['gemini-2.5-flash']
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
        print("--- INICIANDO DIAGNÓSTICO SUPADATA ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⏭️  {vtitle}")
                continue

            print(f"✨ Procesando: {vtitle}")
            transcript = get_transcript_supadata(vid)
            
            if transcript:
                print(f"   📜 Transcripción OK ({len(transcript)} caracteres).")
                html = generate_news(transcript, vtitle)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO EN BD")
                else:
                    print("   ❌ Error Gemini generando texto")
            else:
                print("   ❌ VIDEO OMITIDO (Fallo en transcripción)")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()