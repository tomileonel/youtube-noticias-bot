import os
import re
import logging
import requests
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig # <--- LO QUE PEDÍA LA DOCU
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

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

# --- HERRAMIENTAS DE PROXY ---
def obtener_proxies_gratis():
    print("      🛡️ Buscando proxies gratuitos...")
    try:
        # Obtenemos una lista de proxies HTTP/HTTPS frescos
        url = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all"
        r = requests.get(url, timeout=10)
        proxies = r.text.strip().split('\n')
        # Filtramos vacíos y devolvemos los primeros 20
        return [p.strip() for p in proxies if p.strip()][:20]
    except:
        return []

def probar_fetch_con_api(api_obj, video_id):
    """Lógica común para buscar subtítulos con una instancia ya configurada"""
    try:
        transcript_list = api_obj.list(video_id)
        
        # Prioridad: Español -> Inglés
        transcript = None
        try:
            transcript = transcript_list.find_transcript(['es', 'es-419'])
        except:
            try:
                transcript = transcript_list.find_transcript(['en'])
            except:
                try:
                    transcript = transcript_list.find_generated_transcript(['es', 'en'])
                except:
                    pass
        
        if not transcript:
            for t in transcript_list:
                transcript = t
                break
        
        if transcript:
            if transcript.language_code not in ['es', 'es-419'] and transcript.is_translatable:
                transcript = transcript.translate('es')
            
            data = transcript.fetch()
            return " ".join([t['text'] for t in data])
            
    except Exception as e:
        # No imprimimos error aquí para no ensuciar el log en cada intento de proxy
        return None
    return None

# --- LA SOLUCIÓN BLINDADA ---
def obtener_transcripcion_inteligente(video_id):
    print(f"   🔹 Iniciando protocolo para {video_id}...")

    # NIVEL 1: Disfraz de User-Agent (Sin Proxy)
    # Según docu: "Overwriting request defaults"
    print("      Intento 1: Spoofing de Headers (User-Agent)...")
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "es-ES,es;q=0.9"
        })
        
        # Inyectamos la sesión como dice la documentación
        ytt_api = YouTubeTranscriptApi(http_client=session)
        texto = probar_fetch_con_api(ytt_api, video_id)
        if texto: return texto
    except Exception as e:
        print(f"      Fallo Nivel 1: {e}")

    # NIVEL 2: Proxies Rotativos con GenericProxyConfig
    # Según docu: "Using other Proxy solutions"
    print("      Intento 2: Rotación de Proxies (GenericProxyConfig)...")
    proxies = obtener_proxies_gratis()
    
    for i, proxy_url in enumerate(proxies):
        print(f"      Testing Proxy {i+1}/{len(proxies)}: {proxy_url}")
        try:
            # Configuración exacta de la documentación
            proxy_conf = GenericProxyConfig(
                http_url=f"http://{proxy_url}",
                https_url=f"http://{proxy_url}"
            )
            
            # Instanciamos con el proxy
            ytt_api_proxy = YouTubeTranscriptApi(proxy_config=proxy_conf)
            
            texto = probar_fetch_con_api(ytt_api_proxy, video_id)
            if texto:
                print("      ✅ ¡ÉXITO CON PROXY!")
                return texto
        except Exception:
            continue # Si falla el proxy, probamos el siguiente

    print("   ❌ IMPOSIBLE: Fallaron todos los métodos.")
    return None

def generate_news(text, title):
    modelos = ['gemini-pro', 'gemini-1.5-flash']
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
        print("--- INICIANDO ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⏭️  {vtitle}")
                continue

            print(f"✨ Procesando: {vtitle}")
            transcript = obtener_transcripcion_inteligente(vid)
            
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
                print("   ❌ SKIPPING (Bloqueo IP)")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()