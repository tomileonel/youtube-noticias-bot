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