# Transcript Rework – Pipeline Prototype

## Vue d’ensemble

Ce dépôt contient un **prototype de pipeline** destiné à améliorer la qualité de transcriptions d’appels affectées par des problèmes fréquents : erreurs de transcription, ponctuation absente, ambiguïtés de diarisation (qui parle), et **suspicion d’omissions**.

La solution respecte les contraintes de l’énoncé technique :
- ❌ **Aucune invention** de contenu
- ❌ **Aucune paraphrase** ni modification sémantique
- ✅ **Timestamps préservés**
- ✅ Améliorations **contextualisées**
- ✅ **Traçabilité complète** des transformations

L’objectif est de produire des améliorations **contrôlées et explicables**, en combinant une **extraction de contexte via LLM** et des **règles déterministes** de post-traitement.

### Format JSON d’entrée (attendu)

Le pipeline attend un objet JSON contenant un identifiant et une liste `messages` (segments), par exemple :

```json
{
  "transcript_id": "example_001",
  "messages": [
    { "speaker": "speaker_0", "start_time": 0.0, "end_time": 2.4, "content": "Bonjour..." },
    { "speaker": "speaker_1", "start_time": 2.5, "end_time": 5.1, "content": "Salut !" }
  ]
}
```

---

## Architecture du pipeline

Le pipeline est composé de **trois étapes principales**, exécutées séquentiellement :

```
Transcription brute
   │
   ▼
[1] QA & Analyse temporelle
   │
   ▼
[2] Inférence de contexte (LLM)
   │
   ▼
[3] Éditeur “safe” & Traçabilité
   │
   ▼
Transcription enrichie + Rapports
```

---

## Étape 1 – QA & Détection d’omissions (`qa.py`)

Cette étape réalise une **validation structurelle et temporelle** de la transcription **sans la modifier**.

### Vérifications effectuées
- Structure des segments (`speaker`, `content`, `start_time`, `end_time`)
- Validité des timestamps (`start_time >= 0`, `end_time > start_time`)
- Cohérence chronologique (ordre temporel, chevauchements)

### Détection d’omissions (simple & sûre)
La détection d’omissions repose sur des **heuristiques temporelles** :
- **Grands écarts** entre segments consécutifs (par défaut ≥ 2 secondes)
- **Segments anormalement longs** (par défaut ≥ 25 secondes)

Ces cas sont signalés comme **`omission_suspects`**, mais **ne sont jamais reconstruits**.

### Sortie
- `qa_report` incluant :
  - `omission_suspects`
  - `overlaps`
  - `long_segments`
  - `warnings` / `errors`
- Les segments invalides sont **signalés** mais **non supprimés**

---

## Étape 2 – Inférence de contexte via LLM (`context_inference_llm.py`)

Un LLM est utilisé **uniquement comme extracteur d’informations structurées**, sans aucune modification du contenu.

### Contexte extrait
- **Domaine** de la conversation (sales, support, recruiting, etc.)
- **Rôles des interlocuteurs** (agent/client, interviewer/candidate…)
- **Candidats glossaire** (outils, acronymes, noms de produits)
- **Erreurs de langue** (orthographe, conjugaison, accords, grammaire)

### Garanties anti-hallucination
- Schéma JSON strict (Pydantic + Structured Outputs)
- Chaque hypothèse doit inclure des **preuves (`evidence_quotes`)**
- Les preuves doivent être des **sous-chaînes exactes** de la transcription
- Les preuves sont **validées** contre le texte original
- Les éléments incertains sont marqués avec une **faible confiance**

Si la validation des preuves échoue, le contexte est marqué comme **faible confiance** pour les étapes suivantes.

---

## Étape 3 – Édition sûre & traçabilité (`editor.py`)

Cette étape applique uniquement des transformations **locales, sûres et explicables**.

### Transformations appliquées
1. **Normalisation de glossaire**
   - Alias → terme canonique
   - Appliquée uniquement pour des entrées à forte confiance

2. **Correction d’erreurs de langue**
   - Uniquement les erreurs à forte confiance (≥ 0.80)
   - Pas de paraphrase ni réécriture stylistique

3. **Suppression des répétitions immédiates**
   - ex : “de de”, “et et”, “oui oui”

4. **Ponctuation légère**
   - Majuscule en début de segment
   - Ajout d’une ponctuation terminale si absente

### Ce qui est préservé
- Timestamps
- Labels de speaker
- Découpage en segments
- Sens global, mot à mot (pas de reformulation)

### Traçabilité
Pour chaque segment :
- Texte original vs texte édité
- Liste des opérations appliquées
- Confiance + source de chaque opération

Toutes les transformations sont enregistrées dans :
`transformation_report.editor.segment_reports`

---

## Périmètre du prototype vs exigences

### Démontré complètement
- ✅ Pipeline de nettoyage
- ✅ Extraction de contexte via LLM (contrôlée)
- ✅ Détection d’omissions (écarts temporels)
- ✅ Traitements contextuels
- ✅ Traçabilité des transformations opérées 

### Démontré partiellement
- ⚠️ Amélioration de diarisation  

---

## Challenges anticipés pour la production

### Ambiguïté du langage oral
Les conversations téléphoniques contiennent des hésitations, répétitions et phrases incomplètes. Il est parfois difficile de distinguer une vraie erreur de transcription d’un langage oral naturel.

---

### Détection d’omissions
Les silences détectés via les timestamps peuvent correspondre à des pauses normales et non à des pertes de contenu.

---

### Fiabilité des sorties LLM
Même avec des sorties structurées, un LLM peut proposer des hypothèses incorrectes.

→ Chaque hypothèse doit être justifiée par des extraits exacts du transcript et filtrée par seuil de confiance.

---

### Coût et passage à l’échelle
L’inférence LLM ajoute de la latence et un coût par transcript.

---

## Métriques d’évaluation possibles

- Taux de corrections effectivement appliquées
- Pourcentage de segments modifiés
- Nombre moyen d’opérations par segment
- Taux de faux positifs sur les corrections
---

## Cas limites et évolutions

### Cas limites
- Les silences peuvent déclencher de faux positifs d’omission
- Mélange de langues dans une même conversation
- Conversations très courtes avec peu de contexte
- Segments qui se chevauchent à cause d’une mauvaise diarisation initiale

---

### Évolutions possibles
- Détection **sémantique** des omissions (rupture de sujet + gap temporel)
- Validation humaine pour les corrections à faible confiance
- Adaptation des seuils selon le type de conversation

---

## Exécution

```bash
py src/pipeline.py --infile data/input/sales_saas_001.json --outfile data/output/sales_saas_001.qa.json
```

> Remarque : prévoir les variables d’environnement (clé OPENAI_API_KEY) via un fichier `.env` 

---