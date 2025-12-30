from __future__ import annotations

from typing import List, Literal, Dict, Any
from pydantic import BaseModel, Field
from openai import OpenAI


# --------- 1) Schema strict (Structured Outputs) ---------

DomainLabel = Literal["sales", "support", "recruiting", "healthcare", "other"]
RoleLabel = Literal["agent", "client", "interviewer", "candidate", "other"]
ErrorType = Literal["spelling", "grammar", "conjugation", "agreement"]
                # orthographe, grammaire, conjugaison, accord

class DomainGuess(BaseModel):
    label: DomainLabel
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quotes: List[str] = Field(default_factory=list, description="Exact substrings from transcript.")

class SpeakerRoleGuess(BaseModel):
    mapped_from_speaker: str  # e.g. "speaker_0"
    role: RoleLabel
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quotes: List[str] = Field(default_factory=list)

class GlossaryCandidate(BaseModel):
    term: str
    aliases_found: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quotes: List[str] = Field(default_factory=list)

class LanguageError(BaseModel):
    error_type: ErrorType
    incorrect_text: str = Field(description="The exact incorrect word/phrase from transcript")
    correct_form: str = Field(description="The corrected version")
    explanation: str = Field(description="Brief explanation of the error")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str = Field(description="The full sentence containing the error (exact substring)")

class ContextExtraction(BaseModel):
    domain_guess: DomainGuess
    participants_guess: List[SpeakerRoleGuess] = Field(default_factory=list)
    glossary_candidates: List[GlossaryCandidate] = Field(default_factory=list)
    language_errors: List[LanguageError] = Field(default_factory=list, description="Spelling and grammar errors detected")
    constraints: List[str] = Field(default_factory=lambda: ["no_invention", "no_paraphrase", "preserve_timestamps"])


# --------- 2) Validation anti-hallucination ---------

def _full_text(transcript_obj: Dict[str, Any]) -> str:
    return "\n".join(str(m.get("content", "")) for m in transcript_obj.get("messages", []))

def validate_evidence_quotes(extracted: ContextExtraction, transcript_text: str) -> Dict[str, Any]:
    """
    Returns a report. Does NOT modify extracted; just reports issues.
    """
    def check_quotes(quotes: List[str]) -> List[str]:
        missing = []
        for q in quotes:
            if not q:
                continue
            if q not in transcript_text:
                missing.append(q)
        return missing

    report = {"missing_quotes": [], "ok": True}

    # domain evidence
    report["missing_quotes"] += check_quotes(extracted.domain_guess.evidence_quotes)

    # roles evidence
    for p in extracted.participants_guess:
        report["missing_quotes"] += check_quotes(p.evidence_quotes)

    # glossary evidence
    for g in extracted.glossary_candidates:
        report["missing_quotes"] += check_quotes(g.evidence_quotes)

    # language errors evidence
    for e in extracted.language_errors:
        if e.evidence_quote and e.evidence_quote not in transcript_text:
            report["missing_quotes"].append(e.evidence_quote)

    if report["missing_quotes"]:
        report["ok"] = False

    return report


# --------- 3) LLM call (Structured Outputs) ---------

SYSTEM = """Vous êtes un extracteur d'informations strict pour les transcriptions d'appels.

Règles:
- NE PAS inventer de faits.
- NE PAS paraphraser ou réécrire la transcription.
- La sortie DOIT respecter le schéma JSON fourni.
- Chaque hypothèse DOIT inclure 1 à 3 evidence_quotes qui sont des sous-chaînes EXACTES de la transcription.
- En cas d'incertitude: définir une confiance faible et laisser evidence_quotes vide.
- Pour les erreurs de langue: signaler UNIQUEMENT les erreurs claires, PAS les expressions familières ou les structures de langage parlé naturel.
"""

def infer_context_llm(transcript_obj: Dict[str, Any], model: str = "gpt-5.2") -> Dict[str, Any]:
    client = OpenAI()

    text = _full_text(transcript_obj)
    speakers = sorted({str(m.get("speaker", "unknown")) for m in transcript_obj.get("messages", [])})
    user_prompt = f"""
Intervenants de la transcription: {speakers}

Transcription (verbatim):
{text}

Tâches:
1) Deviner le domaine parmi: sales, support, recruiting, healthcare, etc...
2) Deviner les rôles des intervenants (agent/client ou interviewer/candidate, etc...) si possible.
3) Extraire les candidats du glossaire: noms propres, acronymes, noms de produits/outils; inclure aliases_found uniquement s'ils sont vus.
4) Détecter les erreurs de français (orthographe, grammaire, conjugaison, accord, absence d'accent et d'apostrophe, etc...).
5) Ne pas oublier aussi de corriger les absences d'accent sur les mots.
NE PAS signaler:
- Les expressions familières (genre "c'est cool", "ça le fait")
- Les phrases incomplètes (naturelles à l'oral)
- Les expressions informelles ou régionalismes
- Les hésitations normales du langage parlé

Important:
- evidence_quotes doit être des sous-chaînes EXACTES de la transcription
- Pour language_errors: evidence_quote doit contenir la phrase complète avec l'erreur
- incorrect_text doit être le mot/phrase exact qui est incorrect
- Signaler uniquement les erreurs dont vous êtes sûr (confidence >= 0.7)
"""

    # Structured Outputs via responses.parse (Pydantic schema)
    resp = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        text_format=ContextExtraction,
    )

    extracted: ContextExtraction = resp.output_parsed
    
    # Anti-hallcuination guard: Validate evidence quotes against original text
    ev_report = validate_evidence_quotes(extracted, text)

    # Build context_inferred (what the next steps will consume)
    context_inferred = {
        "domain": extracted.domain_guess.label,
        "domain_confidence": extracted.domain_guess.confidence,
        "speaker_role_map": {
            p.mapped_from_speaker: p.role for p in extracted.participants_guess
        },
        "glossary": [
            {
                "term": g.term,
                "aliases": g.aliases_found,
                "confidence": g.confidence,
            }
            for g in extracted.glossary_candidates
        ],
        "language_errors": [
            {
                "error_type": e.error_type,
                "incorrect_text": e.incorrect_text,
                "correct_form": e.correct_form,
                "explanation": e.explanation,
                "confidence": e.confidence,
            }
            for e in extracted.language_errors
        ],
        "constraints": extracted.constraints,
    }

    context_report = {
        "domain_evidence": extracted.domain_guess.evidence_quotes,
        "participants": [
            {
                "speaker": p.mapped_from_speaker,
                "role": p.role,
                "confidence": p.confidence,
                "evidence": p.evidence_quotes,
            }
            for p in extracted.participants_guess
        ],
        "glossary_evidence": [
            {"term": g.term, "evidence": g.evidence_quotes} for g in extracted.glossary_candidates
        ],
        "language_errors_detected": [
            {
                "error_type": e.error_type,
                "incorrect": e.incorrect_text,
                "correct": e.correct_form,
                "explanation": e.explanation,
                "confidence": e.confidence,
                "evidence": e.evidence_quote,
            }
            for e in extracted.language_errors
        ],
        "evidence_validation": ev_report,
        "source": "openai_structured_outputs",
        "model": model,
    }

    # Hard rule: if evidence validation fails, Flag the context as unreliable
    if not ev_report["ok"]:
        context_report["warning"] = "Evidence quotes validation failed; downstream steps should treat context as low-confidence."

    return {"context_inferred": context_inferred, "context_report": context_report}
