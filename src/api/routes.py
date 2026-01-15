from flask import Blueprint, jsonify, request
import os

from src.rag.chain import RAGChain

api_bp = Blueprint("api", __name__)


@api_bp.route("/qa", methods=["POST"])
def qa():
    """Accepts JSON {"question": "..."} and returns {"question":"...","answer":"..."}.

    Implements a simple RAG flow: retrieve relevant doc chunks and use LangChain/OpenAI if configured.
    """
    data = request.get_json(silent=True) or {}
    question = data.get("question") if isinstance(data, dict) else None
    if not question:
        return jsonify({"error": "missing question"}), 400

    # Initialize chain (this will build index if needed)
    try:
        chain = RAGChain()
        answer = chain.answer(question)
    except Exception as e:
        # fallback to simple answer
        answer = _simple_answer(question) + f"\n\n(Note: RAG chain error: {e})"

    return jsonify({"question": question, "answer": answer}), 200
