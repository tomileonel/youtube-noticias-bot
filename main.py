from youtube_transcript_api import YouTubeTranscriptApi

def ejecutar_cron():
    video_id = "iMgYVJpQQv8" # Aquí puedes poner una lista si quieres
    print(f"Iniciando extracción para: {video_id}")
    
    try:
        ytt_api = YouTubeTranscriptApi()
        fetched_transcript = ytt_api.fetch(video_id, languages=['es', 'en'])
        
        texto_final = " ".join([snippet.text for snippet in fetched_transcript])
        
        with open(f"transcripcion_{video_id}.txt", "w", encoding="utf-8") as f:
            f.write(texto_final)
            
        print("Finalizado con éxito.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    ejecutar_cron()