# Contribuer à Hearthstone Replay Analyzer

Merci de vouloir améliorer HSCoach. Le projet privilégie toujours un fait incomplet
mais prouvé à une interprétation plausible du replay.

## Préparer l’environnement

Python 3.11 ou plus récent est requis. Depuis PowerShell :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,gui,build]"
```

## Avant une proposition

Lisez intégralement `AGENTS.md`, puis exécutez :

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Une correction sémantique doit être accompagnée d’une petite fixture synthétique et
anonyme. Les tests normaux ne doivent dépendre ni d’Internet, ni d’un replay privé, ni
du cache local de cartes.

## Règles de données

- Ne commitez jamais un replay réel, un rapport généré ou un cache HearthstoneJSON.
- Ne publiez aucune URL signée, même expirée ou tronquée après sa query string.
- N’ajoutez pas de BattleTag, de nom de compte ou d’identifiant de compte à une fixture.
- Ne déduisez pas une causalité, une identité cachée ou un choix que le protocole ne
  prouve pas.
- Pour une nouvelle interaction Hearthstone, vérifiez les enums et API installés avant
  d’ajouter une règle.

Les replays locaux peuvent servir à une vérification privée et facultative, mais le
cas essentiel doit aussi être couvert sans eux.

## Portée des changements

Préférez une modification ciblée, documentée et testable. La CLI et l’interface
graphique doivent utiliser `AnalysisService`; aucune logique Hearthstone ne doit être
dupliquée dans l’UI. Une évolution incompatible d’un JSON public exige une nouvelle
version de son schéma et des tests de contrat.

Les contributions ne doivent pas ajouter d’assets Blizzard ni de mécanisme de scraping
de HSReplay.net. Signalez clairement les limites restantes dans la proposition.
