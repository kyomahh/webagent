from task1_rag.document_loader import load_local_manual, preprocess_and_split_basic
from task1_rag.vector_store import build_rag_vector_db_basic
from task1_rag.retriever import rag_retrieve_for_features
from task1_rag.scenario_generator import extract_features_basic, generate_scenarios_basic


def run_task1():
    print("=" * 60)
    print("任务一：基于用户手册的测试场景自动生成")
    print("=" * 60)

    print("\n[1/5] 加载本地手册...")
    documents = load_local_manual()

    print("\n[2/5] 文档预处理与分块...")
    chunks = preprocess_and_split_basic(documents)

    print("\n[3/5] 构建RAG向量库...")
    build_rag_vector_db_basic(chunks)

    print("\n[4/5] 检索并提取功能点...")
    context = rag_retrieve_for_features()
    features = extract_features_basic(context)
    for f in features:
        print(f"  - {f.get('feature_id', '')}: {f.get('feature_name', '')}")

    print("\n[5/5] 生成测试场景...")
    scenarios = generate_scenarios_basic(features, context)
    for s in scenarios:
        print(f"  - {s.get('scenario_id', '')}: {s.get('scenario_name', '')}")

    print("\n任务一完成！结果已保存到 output/ 目录")
    return features, scenarios
