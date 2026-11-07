from huggingface_hub import hf_hub_download

archive = hf_hub_download(
    repo_id='Quanganh6905/Embedding_for_searchengine',
    repo_type='dataset',
    filename='artifacts/siglip2-base-224-t4x2-full-v1/culture-embeddings.zip',
    revision='main',
)
print(archive)
