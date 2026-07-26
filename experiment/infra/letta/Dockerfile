FROM letta/letta:0.16.8

COPY patches/patch_gemini_function_response_role.py /tmp/patch_gemini_function_response_role.py
RUN python /tmp/patch_gemini_function_response_role.py \
    && rm /tmp/patch_gemini_function_response_role.py

COPY patches/patch_google_ai_embeddings.py /tmp/patch_google_ai_embeddings.py
RUN python /tmp/patch_google_ai_embeddings.py \
    && rm /tmp/patch_google_ai_embeddings.py

LABEL org.opencontainers.image.title="financial-memory-letta"
LABEL org.opencontainers.image.description="Letta 0.16.8 with Gemini 3.6 and Google AI embedding compatibility patches"
LABEL org.opencontainers.image.version="0.16.8-googlecompat1"
