from langchain_community.vectorstores import Chroma
from task1_rag.vector_store import load_vector_db


def rag_retrieve_basic(query, k=4):
    vectorstore = load_vector_db()
    results = vectorstore.similarity_search_with_score(query, k=k)
    retrieved_texts = []
    for doc, score in results:
        retrieved_texts.append({
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": float(score),
        })
    return retrieved_texts


def rag_retrieve_for_features(k=6):
    vectorstore = load_vector_db()
    all_docs = vectorstore.similarity_search("4gaboards 功能 特性 feature", k=k)
    context = "\n\n".join([doc.page_content for doc in all_docs])
    return context
