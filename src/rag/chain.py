import os
from typing import List
from dotenv import load_dotenv

# load .env so `GEMINI_API_KEY` is available when this module is imported
load_dotenv()

from src.rag.retriever import RAGRetriever


class RAGChain:
    def __init__(self, retriever: RAGRetriever = None):
        self.retriever = retriever or RAGRetriever()

    def _try_load_langchain_llm(self):
        """Attempt to import LLM helpers lazily. Return tuple (LLMChain, PromptTemplate, llm_factory) or (None, None, None)."""
        try:
            from langchain.chains import LLMChain
            from langchain.prompts import PromptTemplate
        except Exception:
            return None, None, None

        # Provide a factory to create the LLM; prefer Google GenAI if available
        def llm_factory_for_gemini():
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
            except Exception:
                return None

        return LLMChain, PromptTemplate, llm_factory_for_gemini

    def answer(self, question: str) -> str:
        # Step 1: retrieve
        docs = self.retriever.get_top_k(question, k=4)
        context = "\n\n---\n\n".join([d for (_s, d) in docs]) if docs else ""

        # Try to use a configured LLM (Gemini) if available
        if os.getenv("GEMINI_API_KEY"):
            LLMChain, PromptTemplate, llm_factory = self._try_load_langchain_llm()
            if LLMChain is not None and PromptTemplate is not None and llm_factory is not None:
                llm = llm_factory()
                if llm is not None:
                    template = (
                        "Use the provided context to answer the question concisely. "
                        "If the answer is not in the context, say 'I don't know, consult the resources.'\n\n"
                        "Context:\n{context}\n\nQuestion:\n{question}\n\nAnswer:"
                    )
                    prompt = PromptTemplate(template=template, input_variables=["context", "question"])
                    chain = LLMChain(llm=llm, prompt=prompt)
                    resp = chain.run({"context": context, "question": question})
                    return resp
            # If LangChain's LLMChain/PromptTemplate unavailable, try calling Gemini directly
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                # prefer langchain_core.schema.HumanMessage if available
                # Try to find a compatible HumanMessage type from likely langchain modules
                HumanMessage = None
                for mod in (
                    "langchain.schema",
                    "langchain.messages",
                    "langchain_core.schema",
                    "langchain_core.messages",
                    "langchain_core.schema.messages",
                ):
                    try:
                        module = __import__(mod, fromlist=["HumanMessage"])
                        HumanMessage = getattr(module, "HumanMessage", None)
                        if HumanMessage is not None:
                            break
                    except Exception:
                        continue

                llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)

                # Construct chat-style messages expected by the Gemini client
                prompt_text = (
                    "Use the provided context to answer the question concisely. "
                    "If the answer is not in the context, say 'I don't know, consult the resources.'\n\n"
                    f"Context:\n{context}\n\nQuestion:\n{question}\n\nAnswer:"
                )

                # If we found a proper HumanMessage class, use it; otherwise skip direct Gemini call
                if HumanMessage is not None:
                    msg = HumanMessage(content=prompt_text)
                    try:
                        gen = llm.generate([[msg]])
                        try:
                            return gen.generations[0][0].text
                        except Exception:
                            return str(gen)
                    except Exception:
                        import traceback

                        traceback.print_exc()
                        try:
                            if hasattr(llm, "predict"):
                                return llm.predict(prompt_text)
                        except Exception:
                            traceback.print_exc()
                        try:
                            if callable(llm):
                                return llm(prompt_text)
                        except Exception:
                            traceback.print_exc()
                else:
                    # No compatible message class available in this environment; attempt direct Google GenAI client call
                    try:
                        from google.genai.client import Client

                        client = Client(api_key=os.getenv("GEMINI_API_KEY"))
                        # Build contents in the shape expected by the client (role + parts)
                        contents = [{
                            "role": "user",
                            "parts": [{"text": prompt_text}],
                        }]
                        try:
                            resp = client.models.generate_content(
                                model="gemini-2.5-flash", contents=contents
                            )
                            # Try to extract text from response robustly
                            try:
                                candidates = getattr(resp, "candidates", None) or resp.get("candidates")
                            except Exception:
                                candidates = None
                            if candidates:
                                first = candidates[0]
                                content = getattr(first, "content", None) or first.get("content")
                                if content:
                                    parts = getattr(content, "parts", None) or content.get("parts")
                                    if parts:
                                        first_part = parts[0]
                                        text = getattr(first_part, "text", None) or first_part.get("text")
                                        if text:
                                            return text
                            # Fallback to stringifying response
                            return str(resp)
                        except Exception:
                            import traceback

                            traceback.print_exc()
                    except Exception:
                        import logging

                        logging.warning(
                            "No compatible HumanMessage class found and direct GenAI client failed. Falling back to retrieval."
                        )
            except Exception:
                # If direct Gemini call isn't available or fails, log and fall through to retrieval fallback
                import traceback

                traceback.print_exc()
                pass

        # Fallback: return retrieved context concatenated
        if context:
            return "(LLM not configured) Retrieved relevant documents:\n" + context[:4000]
        return "No documents available and LLM not configured."
