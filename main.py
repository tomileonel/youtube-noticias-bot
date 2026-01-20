from youtube_transcript_api import YouTubeTranscriptApi
import sys

def ejecutar_rapido():
    video_id = "iMgYVJpQQv8" # Puedes cambiar el ID aquí
    
    try:
        # Método directo v1.2.3
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        
        texto = " ".join([i['text'] for i in data])
        
        # Guardamos el resultado
        with open("resultado.txt", "w", encoding="utf-8") as f:
            f.write(texto)
        
        print(f"Success: {video_id}")
    except Exception as e:
        print(f"Fail: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_rapido()