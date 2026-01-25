import os
from typing import List
from dotenv import load_dotenv

# load .env so `GEMINI_API_KEY` is available when this module is imported
load_dotenv()

from src.rag.retriever import RAGRetriever
def _postprocess_response(text: str) -> str:
    # passthrough postprocessor (removed centralized postprocess module)
    return (text or "")


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
        # Early short-circuit: for short definition-style queries, prefer an
        # LLM-only answer immediately to avoid any PDF-derived text.
        try:
            import re
            q_norm = (question or "").strip()
            word_count = len(q_norm.split())
            if (re.match(r"^\s*(what|what's|define|explain)\b", q_norm.lower()) or word_count <= 8) and os.getenv("GEMINI_API_KEY"):
                # Directly call the GenAI client here to bypass retrieval completely.
                try:
                    from google.genai.client import Client
                    prompt_text = (
                        "Answer concisely: definition, causes, coping strategies, when to seek help. "
                        "Do not insert spaces inside words.\n\nQuestion:\n" + q_norm + "\n\nAnswer:"
                    )
                    client = Client(api_key=os.getenv("GEMINI_API_KEY"))
                    contents = [{"role": "user", "parts": [{"text": prompt_text}]}]
                    resp = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
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
                                    return _postprocess_response(text)
                except Exception:
                    pass
        except Exception:
            pass
        # Retrieval-first: always run the retriever first and prefer document
        # context when available. LLMs will be used later as a summarizer or
        # fallback when retrieved context is not sufficiently relevant.
        candidates = self.retriever.get_top_k(question, k=50)

        # Apply a simple re-ranking that boosts files whose filename
        # tokens appear in the question. Because Faiss distances are
        # L2 (lower = better), we subtract a small boost amount from
        # the distance for matches to prefer them.
        import re

        q_l = question.lower()

        def filename_tokens(src: str):
            if not src:
                return []
            s = src.lower().replace('_', ' ')
            return [t for t in re.findall(r"\w+", s) if len(t) > 3]

        boosted = []
        for (score, text, meta) in candidates:
            boost = 0.0
            src = ''
            if isinstance(meta, dict):
                src = (meta.get('source') or '').lower()
            for tok in filename_tokens(src):
                if tok in q_l:
                    # strong filename match -> subtract boost
                    boost += 0.55
                # favour partial token overlap (e.g., 'forgive' ~ 'forgiveness')
                if tok and tok[:-2] in q_l:
                    boost += 0.25
            # also give a small preference if the doc text itself contains
            # a question token (without requiring filename match)
            q_words = [w.lower() for w in re.findall(r"[\w']{4,}", question) if len(w) > 3]
            for w in q_words:
                if w in (text or '').lower():
                    boost += 0.15

            # produce an adjusted score (lower is better). Keep as float
            adj_score = float(score) - float(boost)
            boosted.append((adj_score, text, meta))

        # sort by adjusted score (ascending) and pick the top few to work with
        boosted.sort(key=lambda x: x[0])
        docs = boosted[:4]
        context = "\n\n---\n\n".join([d for (_s, d, _m) in docs]) if docs else ""

        # If the question appears to be a short definition/request (e.g. "What is X?"),
        # prefer an LLM-only answer (discard PDFs) to avoid returning noisy PDF snippets.
        try:
            import re
            if re.match(r"^\s*(what|what's|define|explain)\b", question.strip().lower()):
                if os.getenv("GEMINI_API_KEY"):
                    res = _call_direct_llm(question)
                    if isinstance(res, str) and res:
                        return res
        except Exception:
            pass

        # Helper: simple extractive summarizer when no LLM is configured
        def _simple_summarize(topic: str, docs_list):
            import re

            def _clean_text(s: str) -> str:
                # collapse whitespace and insert spaces before camel-cased words
                s = re.sub(r"\s+", " ", s or "").strip()
                # insert space between lower->Upper transitions (e.g. "DepressionStress" -> "Depression Stress")
                s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
                return s

            topic_l = topic.lower()
            found = []
            # priority keywords for causes/risk factors
            keywords = [
                'cause', 'caus', 'risk', 'associated', 'predispos', 'stress', 'trauma',
                'genetic', 'biolog', 'psycholog', 'social', 'loss', 'bereav', 'unemploy', 'workplace'
            ]

            for (_s, text, meta) in docs_list:
                t = _clean_text(text)
                # split into sentences by punctuation
                sents = re.split(r'(?<=[.!?])\s+', t)
                for sent in sents:
                    low = sent.lower()
                    # pick sentences that mention the topic or any cause-related keyword
                    if topic_l in low or any(k in low for k in keywords):
                        sent = sent.strip()
                        if sent and sent not in found:
                            found.append(sent if sent.endswith('.') else sent + '.')
                    if len(found) >= 5:
                        break
                if len(found) >= 5:
                    break

            if found:
                # Prefer sentences that explicitly mention cause/risk; if we have many,
                # return the most relevant up to a short paragraph.
                return ' '.join(found[:5])

            # Fallback: try to find the topic substring and return a small window around it
            if context:
                ctx = _clean_text(context)
                idx = ctx.lower().find(topic_l)
                if idx != -1:
                    start = max(0, idx - 200)
                    end = min(len(ctx), idx + 400)
                    snippet = ctx[start:end].strip()
                    # ensure snippet ends at sentence boundary if possible
                    last_dot = snippet.rfind('.')
                    if last_dot > 50:
                        snippet = snippet[: last_dot + 1]
                    return snippet

            return (context[:500] + '...') if context else ''

        # Post-process LLM text to fix common spacing/artifact issues while
        # remaining conservative for production use. This removes repeated
        # whitespace, trims spaces before punctuation, and attempts to
        # rejoin fragmented words like 't o' -> 'to' or 'intimat e' -> 'intimate'
        def _postprocess_response(text: str) -> str:
            # Delegate to the centralized postprocessing utility to ensure
            # consistent de-fragmentation across API and chain paths.
            try:
                return postprocess_text(text or "")
            except Exception:
                return text or ""

            # Conservative fixes for common fused tokens produced by some LLM outputs
            # e.g., 'witha' -> 'with a', 'talkto' -> 'talk to', 'speakto' -> 'speak to'
            fused_map = {
                'witha': 'with a',
                'talkto': 'talk to',
                'speakto': 'speak to',
                'speakwith': 'speak with',
                'talkwith': 'talk with',
                'goto': 'go to',
                'gointo': 'go into',
            }
            # apply replacements case-insensitively while preserving surrounding whitespace/punctuation
            for k, v in fused_map.items():
                out = re.sub(rf'(?i)\b{k}\b', v, out)

            # Aggressive de-fragmentation: join sequences of short letter-groups
            # like 'ps ychotic s ympt oms' -> 'psychotic symptoms'. Match
            # runs of 3+ alpha-groups separated by spaces and remove internal spaces.
            out = re.sub(
                r"\b(?:[A-Za-z]{1,10}\s+){2,}[A-Za-z]{1,10}\b",
                lambda m: re.sub(r"\s+", "", m.group(0)),
                out,
            )

            # final whitespace normalize after transformations
            out = re.sub(r"\s+", " ", out).strip()
            return out

        # Helper: call the configured LLM (LangChain or direct Google GenAI client)
        # Returns postprocessed string or None on failure.
        def _call_direct_llm(q: str):
            try:
                LLMChain, PromptTemplate, llm_factory = self._try_load_langchain_llm()
            except Exception:
                LLMChain = PromptTemplate = llm_factory = None

            prompt_template = (
                "Provide a concise, practical answer to the user's question. "
                "Include a brief definition, common causes or triggers if relevant, "
                "practical coping strategies, and guidance on when to seek professional help. "
                "Be empathetic and concise. Do NOT insert extra spaces inside words or between letters; return cleanly formatted text.\n\nQuestion:\n{question}\n\nAnswer:"
            )

            # Try LangChain LLMChain first if available
            if LLMChain is not None and PromptTemplate is not None and llm_factory is not None:
                try:
                    llm = llm_factory()
                    if llm is not None:
                        prompt = PromptTemplate(template=prompt_template, input_variables=["question"])
                        chain_lc = LLMChain(llm=llm, prompt=prompt)
                        resp = chain_lc.run({"question": q})
                        if isinstance(resp, str) and resp:
                            return _postprocess_response(resp)
                except Exception:
                    pass

            # Try direct Google GenAI client
            try:
                from google.genai.client import Client
                client = Client(api_key=os.getenv("GEMINI_API_KEY"))
                contents = [{"role": "user", "parts": [{"text": prompt_template.format(question=q)}]}]
                resp = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
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
                                return _postprocess_response(text)
            except Exception:
                pass

            return None

        # Helper: build a tailored answer from related documents when no
        # directly relevant file exists. This attempts to extract sentences
        # from top-related documents and arrange them into a short, topic-
        # focused summary (definition, causes, coping, when to seek help).
        def _tailored_from_related_docs(topic: str):
            try:
                related = self.retriever.get_top_k(topic, k=20)
            except Exception:
                return None
            import re
            q_words = [w.lower() for w in re.findall(r"[\w']{3,}", topic) if len(w) > 2]
            # sentence buckets
            buckets = {'definition': [], 'causes': [], 'coping': [], 'seek_help': []}
            cause_kw = ('cause', 'risk', 'because', 'lead', 'trigger', 'predis')
            coping_kw = ('coping', 'manage', 'strategy', 'technique', 'practice', 'exercise', 'help', 'support')
            seek_kw = ('seek', 'professional', 'therapy', 'counsel', 'urgent', 'emergency', 'crisis')

            def sentences_from(text: str):
                if not text:
                    return []
                sents = re.split(r'(?<=[.!?])\s+', text.replace('\n', ' '))
                return [s.strip() for s in sents if len(s.strip()) > 30]

            for (_s, text, meta) in related:
                for sent in sentences_from(text):
                    low = sent.lower()
                    # definition-like heuristic: contains 'is' or 'refers to'
                    if any(w in low for w in q_words) and (' is ' in low or ' refers to ' in low or 'means ' in low):
                        if len(buckets['definition']) < 2:
                            buckets['definition'].append(sent)
                        continue
                    if any(k in low for k in cause_kw):
                        if len(buckets['causes']) < 3:
                            buckets['causes'].append(sent)
                        continue
                    if any(k in low for k in coping_kw):
                        if len(buckets['coping']) < 4:
                            buckets['coping'].append(sent)
                        continue
                    if any(k in low for k in seek_kw):
                        if len(buckets['seek_help']) < 2:
                            buckets['seek_help'].append(sent)
                        continue
                    # fallback: if sentence contains a query token prefix, use it
                    for w in q_words:
                        if w in low or (len(w) > 4 and w[:4] in low):
                            if len(buckets['coping']) < 4:
                                buckets['coping'].append(sent)
                            break

            # If we didn't find any sentences, give up
            total_found = sum(len(v) for v in buckets.values())
            if total_found == 0:
                return None

            parts = []
            if buckets['definition']:
                parts.append('Definition: ' + ' '.join(buckets['definition'][:2]))
            if buckets['causes']:
                parts.append('Causes/Risk factors: ' + ' '.join(buckets['causes'][:3]))
            if buckets['coping']:
                parts.append('Coping strategies: ' + ' '.join(buckets['coping'][:4]))
            if buckets['seek_help']:
                parts.append('When to seek help: ' + ' '.join(buckets['seek_help'][:2]))

            # Always add a short tailored lead acknowledging no exact-source
            lead = f"I couldn't find a document explicitly titled for '{topic}', but here is a focused summary assembled from related resources in the collection."
            return lead + '\n\n' + '\n\n'.join(parts)

        # Detect if query references a topic present in any source metadata
        q_l = question.lower()
        matched_topic = None
        for (_s, _d, meta) in docs:
            src = meta.get('source', '') if isinstance(meta, dict) else ''
            src_l = src.lower()
            # check for short tokens from filename (e.g., 'trauma' in 'healing_from_trauma.pdf')
            for token in [t for t in src_l.replace('_', ' ').split() if len(t) > 3]:
                if token in q_l:
                    matched_topic = token
                    break
            if matched_topic:
                break

        # If we found a filename token that matches the query, prefer chunks
        # from files containing that token. This helps avoid unrelated docs
        # being used when a specific topical file exists (e.g., forgiveness).
        if matched_topic:
            filtered = [t for t in docs if isinstance(t[2], dict) and matched_topic in (t[2].get('source', '').lower())]
            if filtered:
                docs = filtered
                context = "\n\n---\n\n".join([d for (_s, d, _m) in docs]) if docs else ""

        # If no filename token matched, try to find meaningful tokens from the
        # question that appear inside the retrieved document text. For example,
        # a question containing 'forgiveness' should prefer docs that contain
        # the word 'forgiveness' even if the filename does not include it.
        if not matched_topic and docs:
            import re

            q_words = [w.lower() for w in re.findall(r"[\w']{4,}", question) if len(w) > 3]
            found_token = None
            for w in q_words:
                # skip very common words
                if w in ('about', 'what', 'when', 'where', 'why', 'how', 'the', 'and', 'with'):
                    continue
                for (_s, text, meta) in docs:
                    if text and w in text.lower():
                        found_token = w
                        break
                if found_token:
                    break

            if found_token:
                filtered = [t for t in docs if (t[1] and found_token in t[1].lower())]
                if filtered:
                    docs = filtered
                    context = "\n\n---\n\n".join([d for (_s, d, _m) in docs]) if docs else ""

        # Additional fallback: if none of the retrieved chunks contain any of
        # the meaningful question tokens, try matching the tokens against
        # filenames in the retriever's docstore. This helps when the index
        # returned related documents but a specific topical file exists (e.g.
        # 'forgiveness.pdf').
        try:
            import re
            q_words = [w.lower() for w in re.findall(r"[\w']{4,}", question) if len(w) > 3]
            common = {'about', 'what', 'when', 'where', 'why', 'how', 'the', 'and', 'with'}

            def _docs_have_any_token(docs_list, tokens):
                for (_s, text, meta) in docs_list:
                    txt = (text or '').lower()
                    src = ''
                    if isinstance(meta, dict):
                        src = (meta.get('source') or '').lower()
                    for t in tokens:
                        if t in common:
                            continue
                        if t in txt or t in src:
                            return True
                return False

            if docs and not _docs_have_any_token(docs, q_words):
                store = getattr(self.retriever, 'docstore', []) or []
                matched = []
                for item in store:
                    src = (item.get('source') or '').lower()
                    for w in q_words:
                        if w in common:
                            continue
                        if w in src:
                            matched.append(item)
                            break
                if matched:
                    # prefer these matched file chunks as the new context
                    docs = [(0.0, m.get('text', ''), m) for m in matched[:4]]
                    context = "\n\n---\n\n".join([d for (_s, d, _m) in docs]) if docs else ""
        except Exception:
            # best-effort only; don't raise on errors here
            pass

        # Heuristic: if retrieved docs exist but are not meaningfully relevant
        # to the question (very low token overlap), prefer calling the LLM
        # directly rather than using fragmented or off-topic PDF snippets.
        def _docs_relevance_score(topic: str, docs_list) -> float:
            import re
            if not docs_list:
                return 0.0
            q_words = [w.lower() for w in re.findall(r"[\w']{4,}", topic) if len(w) > 3]
            if not q_words:
                return 0.0
            matched = 0
            for w in q_words:
                for (_s, text, meta) in docs_list:
                    txt = (text or '').lower()
                    src = ''
                    if isinstance(meta, dict):
                        src = (meta.get('source') or '').lower()
                    if w in txt or w in src:
                        matched += 1
                        break
            return matched / float(len(q_words))

        try:
            relevance = _docs_relevance_score(question, docs)
            # threshold: if less than 25% of meaningful tokens appear anywhere
            # in the retrieved snippets or filenames, treat as no meaningful context
            if context and relevance < 0.25:
                # Treat as no meaningful context. Prefer calling the LLM directly
                # and avoid using PDF snippets for answers when an LLM is configured.
                context = ""
                docs = []
                # If an LLM is available, short-circuit to a direct LLM-only answer
                if os.getenv("GEMINI_API_KEY"):
                    res = _call_direct_llm(question)
                    if isinstance(res, str) and res:
                        return res
        except Exception:
            # non-fatal; if the scoring fails, continue with existing context
            pass

        # If there is no meaningful context from local documents, but an
        # LLM is configured, prefer calling the LLM directly to answer the
        # question rather than returning a generic fallback. This handles
        # queries like 'FOMO' where no explicit document exists.
        if (not context or len(context.strip()) < 50) and os.getenv("GEMINI_API_KEY"):
            LLMChain, PromptTemplate, llm_factory = self._try_load_langchain_llm()
            prompt_template = (
                "Provide a concise, practical answer to the user's question. "
                "Include a brief definition, common causes or triggers if relevant, "
                "practical coping strategies, and guidance on when to seek professional help. "
                "Be empathetic and concise. Do NOT insert extra spaces inside words or between letters; return cleanly formatted text.\n\nQuestion:\n{question}\n\nAnswer:"
            )
            # Try using LangChain LLMChain if available
            if LLMChain is not None and PromptTemplate is not None and llm_factory is not None:
                try:
                    llm = llm_factory()
                    if llm is not None:
                        prompt = PromptTemplate(template=prompt_template, input_variables=["question"])
                        chain = LLMChain(llm=llm, prompt=prompt)
                        resp = chain.run({"question": question})
                        if resp:
                            return _postprocess_response(resp)
                except Exception:
                    pass

            # Fallback: try direct Gemini client or langchain_google_genai as before
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                HumanMessage = None
                for mod in (
                    "langchain.schema",
                    "langchain.messages",
                    "langchain_core.schema",
                    "langchain_core.messages",
                ):
                    try:
                        module = __import__(mod, fromlist=["HumanMessage"])
                        HumanMessage = getattr(module, "HumanMessage", None)
                        if HumanMessage is not None:
                            break
                    except Exception:
                        continue

                llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
                prompt_text = prompt_template.format(question=question)
                if HumanMessage is not None:
                    msg = HumanMessage(content=prompt_text)
                    try:
                        gen = llm.generate([[msg]])
                        try:
                            text = gen.generations[0][0].text
                            if text:
                                return _postprocess_response(text)
                        except Exception:
                            return str(gen)
                    except Exception:
                        pass
                else:
                    try:
                        from google.genai.client import Client
                        client = Client(api_key=os.getenv("GEMINI_API_KEY"))
                        contents = [{"role": "user", "parts": [{"text": prompt_text}]}]
                        resp = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
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
                                        return _postprocess_response(text)
                    except Exception:
                        pass

            finally:
                pass
        # Try to use a configured LLM (Gemini) if available
        if os.getenv("GEMINI_API_KEY"):
            LLMChain, PromptTemplate, llm_factory = self._try_load_langchain_llm()
            if LLMChain is not None and PromptTemplate is not None and llm_factory is not None:
                llm = llm_factory()
                if llm is not None:
                    template = (
                        "Use the provided context to answer the question concisely. "
                        "If the answer is not in the context, say 'I don't know, consult the resources.'\n\n"
                        "Context:\n{context}\n\nQuestion:\n{question}\n\nAnswer: (Please return cleanly formatted text without inserting spaces inside words)"
                    )
                    prompt = PromptTemplate(template=template, input_variables=["context", "question"])
                    chain = LLMChain(llm=llm, prompt=prompt)
                    resp = chain.run({"context": context, "question": question})
                    # If LLM returns a non-answer, fall back to extractive summary
                    if resp and isinstance(resp, str):
                        low = resp.lower()
                        if "i don't know" in low or "don't know" in low or "consult the resources" in low or "not in the context" in low:
                            # synthesize from retrieved docs instead and postprocess
                            summary = _simple_summarize(question, docs)
                            if summary:
                                return _postprocess_response(summary)
                    return _postprocess_response(resp)
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
                            text = gen.generations[0][0].text
                            low = text.lower() if isinstance(text, str) else ""
                            if "i don't know" in low or "don't know" in low or "consult the resources" in low or "not in the context" in low:
                                summary = _simple_summarize(question, docs)
                                if summary:
                                    return _postprocess_response(summary)
                            return _postprocess_response(text)
                        except Exception:
                            return str(gen)
                    except Exception:
                        import traceback

                        traceback.print_exc()
                        try:
                            if hasattr(llm, "predict"):
                                res = llm.predict(prompt_text)
                                if isinstance(res, str):
                                    return _postprocess_response(res)
                                return str(res)
                        except Exception:
                            traceback.print_exc()
                        try:
                            if callable(llm):
                                res = llm(prompt_text)
                                if isinstance(res, str):
                                    return _postprocess_response(res)
                                return str(res)
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
                                            low = text.lower() if isinstance(text, str) else ""
                                            if "i don't know" in low or "don't know" in low or "consult the resources" in low or "not in the context" in low:
                                                summary = _simple_summarize(question, docs)
                                                if summary:
                                                    return _postprocess_response(summary)
                                            return _postprocess_response(text)
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

        # Fallback when no LLM available: always try to synthesize an extractive
        # answer from the retrieved documents (searching document text for the
        # question). This prevents returning raw concatenated context to the
        # user when the retriever is the only component.
        if not os.getenv("GEMINI_API_KEY"):
            summary = _simple_summarize(question, docs)
            if summary:
                return "(No LLM) " + _postprocess_response(summary)

            # If summarization failed and we have some context, return it
            if context:
                return "(LLM not configured) Retrieved relevant documents:\n" + context[:4000]

            # Try to assemble a tailored, topic-specific answer from related
            # documents even when no exact-match file exists. This produces a
            # more useful, non-generic response (e.g., for queries like
            # 'FOMO') by extracting relevant sentences from related docs.
            tailored = _tailored_from_related_docs(question)
            if tailored:
                return tailored

            # As a last resort, provide a short, safe generic answer for
            # common mental-health topics so users still get useful help
            # even when no directly relevant PDF text exists.
            generic = {
                'depress': (
                    "Depression is a common mental health condition characterized by persistent low mood, "
                    "loss of interest, changes in appetite or sleep, and difficulty concentrating. If you or "
                    "someone is struggling, consider reaching out to a healthcare professional, a trusted person, "
                    "or local mental health services. In immediate danger or crisis, call your local emergency number."
                ),
                'anxi': (
                    "Anxiety commonly causes excessive worry, restlessness, muscle tension, and difficulty "
                    "sleeping. Techniques like slow breathing, grounding exercises, and structured routines can help. "
                    "If symptoms persist or interfere with daily life, seek support from a clinician or counselor."
                ),
                'stress': (
                    "Stress reactions are normal. Practical strategies include taking short breaks, prioritizing tasks, "
                    "using relaxation techniques, and reaching out for social support. If stress becomes chronic, "
                    "professional help can be beneficial."
                ),
            }
            ql = question.lower()
            for k, v in generic.items():
                if k in ql:
                    return v

            # No LLM, no docs, and no specific generic match — give a neutral fallback.
            return (
                "I couldn't find documents specific to that question, but general resources and professional support "
                "can help. Try rephrasing the question or consult local mental-health services for tailored guidance."
            )

        # If we reach here, LLM was configured but earlier attempts failed;
        # return concatenated context as a last resort.
        if context:
            return "Retrieved relevant documents:\n" + context[:4000]
        return "No documents available."
