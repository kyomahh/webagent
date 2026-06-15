import os
import glob
from bs4 import BeautifulSoup
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

MANUAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manual")


def load_local_manual():
    documents = []
    if not os.path.exists(MANUAL_DIR):
        raise FileNotFoundError(f"手册目录不存在: {MANUAL_DIR}，请先将4gaboards用户手册下载到manual/文件夹")

    for filepath in sorted(glob.glob(os.path.join(MANUAL_DIR, "**/*"), recursive=True)):
        if not os.path.isfile(filepath):
            continue
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext in (".md", ".markdown"):
                loader = UnstructuredMarkdownLoader(filepath)
                docs = loader.load()
                documents.extend(docs)
            elif ext in (".html", ".htm"):
                with open(filepath, "r", encoding="utf-8") as f:
                    soup = BeautifulSoup(f.read(), "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                documents.append(Document(page_content=text, metadata={"source": filepath}))
            elif ext == ".txt":
                with open(filepath, "r", encoding="utf-8") as f:
                    text = f.read()
                documents.append(Document(page_content=text, metadata={"source": filepath}))
        except Exception as e:
            print(f"加载文件失败 {filepath}: {e}")

    print(f"共加载 {len(documents)} 个文档")
    return documents


def preprocess_and_split_basic(documents, chunk_size=800, chunk_overlap=80):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "，", " ", ""],
    )
    chunks = text_splitter.split_documents(documents)
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = i
    print(f"共分割为 {len(chunks)} 个文本块")
    return chunks
