import os
import glob
import logging
import yt_dlp
import google.generativeai as genai
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# Configuración
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# --- BD ---
db_string = os.getenv('DATABASE_URL')
if db_string and db_string.startswith("postgres://"):
    db_string = db_string.replace("postgres://", "postgresql://", 1)
if not db_string: exit()

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
    try:
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        response = request.execute()
        return [{'id': i['id']['videoId'], 'title': i['snippet']['title']} for i in response.get('items', [])]
    except Exception as e:
        print(f"Error API Youtube: {e}")
        return []

def get_transcript_ytdlp(video_id):
    print(f"DEBUG: Intentando descarga con yt-dlp para {video_id}...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # --- AQUÍ ESTÁ EL TRUCO ---
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['es', 'es-419', 'en'],
        'outtmpl': f'/tmp/{video_id}',
        'quiet': True,
        # Forzamos a que use el cliente de TV o IOS que molestan menos con el login
        'extractor_args': {'youtube': {'player_client': ['ios', 'android', 'web']}},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        files = glob.glob(f"/tmp/{video_id}*.vtt")
        if not files:
            print("DEBUG: yt-dlp no encontró ningún subtítulo.")
            return None
            
        filename = files[0]
        
        clean_text = []
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines:
                if '-->' in line or line.strip() == '' or line.startswith('WEBVTT') or line.strip().isdigit():
                    continue
                line = line.replace('<c>', '').replace('</c>', '').strip()
                if clean_text and clean_text[-1] == line:
                    continue
                clean_text.append(line)
        
        os.remove(filename)
        return " ".join(clean_text)

    except Exception as e:
        print(f"ERROR YT-DLP: {e}")
        return None

def generate_news(text, title):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Eres periodista. Crea noticia HTML para WordPress sobre: '{title}'.
    TRANSCRIPCION: {text[:25000]}
    REGLAS: HTML (<h2>,<p>,<ul>). Tono profesional. +300 palabras.
    """
    try:
        return model.generate_content(prompt).text
    except Exception as e:
        print(f"Error Gemini: {e}")
        return None

def main():
    session = Session()
    try:
        cid = os.getenv('CHANNEL_ID')
        if not cid: return

        videos = get_latest_videos(cid)
        print(f"Analizando {len(videos)} videos recientes...")

        for v in videos:
            vid, vtitle = v['id'], v['title']

            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"[YA EXISTE] {vtitle}")
                continue

            print(f"[NUEVO] Procesando: {vtitle}")
            text = get_transcript_ytdlp(vid)
            
            if not text:
                print(" -- Saltando (Sin audio/texto)")
                continue

            html = generate_news(text, vtitle)
            if html:
                post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                session.add(post)
                session.commit()
                print(" -- ¡GUARDADO EN BD!")
            
    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()