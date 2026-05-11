import os
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import ZhipuAIEmbeddings

CHROMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_db")


def build_rag_vector_db_basic(chunks, batch_size=50):
    embeddings = ZhipuAIEmbeddings(model="embedding-3")
    vectorstore = None
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        if vectorstore is None:
            vectorstore = Chroma.from_documents(
                documents=batch,
                embedding=embeddings,
                persist_directory=CHROMA_DIR,
            )
        else:
            vectorstore.add_documents(documents=batch)
        print(f"  已处理 {min(i + batch_size, len(chunks))}/{len(chunks)} 个文本块")
    vectorstore.persist()
    print(f"向量库构建完成，共 {vectorstore._collection.count()} 条记录")
    return vectorstore


def load_vector_db():
    embeddings = ZhipuAIEmbeddings(model="embedding-3")
    vectorstore = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
    )
    return vectorstore
