# Hearthstone Replay Analyzer

Hearthstone Replay Analyzer (`hscoach`) transforme un replay Hearthstone au format
HSReplay XML en trois rapports factuels, anonymisés et en français : un compte rendu
Markdown, un JSON d'analyse exhaustif et un JSON compact destiné aux LLM.

La version 2 corrige le sens temporel des états de jeu. Chaque demi-tour peut désormais
contenir quatre snapshots distincts, des deltas avant/après et une chronologie enrichie.
L'outil décrit ce que le replay permet d'observer ; il ne note pas les plays, ne conseille
aucune action et ne simule pas de ligne alternative.

La locale par défaut est `frFR`. Une carte non résolue reste explicitement
`Carte inconnue [CARD_ID]` ; aucun nom anglais n'est injecté silencieusement.

## Prérequis

- Python 3.11 ou plus récent ;
- un replay HSReplay XML local, ou une URL HTTP/HTTPS pointant directement vers ce XML ;
- un accès réseau au premier chargement de HearthstoneJSON, sauf si un cache valide existe.

Les principales dépendances sont `hsreplay`, `hslog`, `hearthstone` et `httpx`.

## Installation

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Si l'activation des scripts est bloquée, elle peut être autorisée pour le processus
courant seulement :

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux et macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Remplacez la dernière commande par `python -m pip install -e .` pour ne pas installer
les outils de développement.

## Utilisation

### Analyser un replay local

```bash
python -m hscoach analyser chemin/partie.hsreplay
```

Les extensions `.hsreplay`, `.xml`, `.txt` et l'absence d'extension sont acceptées.
Le contenu est toujours prévalidé comme un document HSReplay XML, indépendamment du
nom du fichier.

Une analyse réussie crée atomiquement :

```text
output/<game-id>/game_summary.md
output/<game-id>/game_analysis.json
output/<game-id>/game_llm.json
```

Le dossier `<game-id>` est neutralisé pour empêcher tout path traversal. Relancer
l'analyse d'une même partie remplace ces trois rapports.

### Analyser une URL XML directe

```bash
python -m hscoach analyser "https://example.com/replay.hsreplay.xml?signature=..."
```

Seuls HTTP et HTTPS sont autorisés. Le téléchargement est effectué en streaming avec
un timeout de 20 secondes et une limite de 50 Mio par défaut. Une URL publique de page
comme `https://hsreplay.net/replay/<ID>` n'est pas prise en charge : `hscoach` ne scrape
pas le site HSReplay et attend une URL directe vers le XML.

La query string d'une URL distante n'est ni loggée ni copiée dans les rapports, y
compris en mode verbeux.

### Menu et autres commandes

```bash
python -m hscoach
python -m hscoach inspecter chemin/partie.hsreplay
python -m hscoach actualiser-cartes
python -m hscoach configuration
```

Le menu interactif reprend les mêmes opérations. `inspecter` analyse sans écrire de
rapport et affiche les diagnostics principaux du replay. `configuration` affiche les
valeurs actives : locale, anonymisation, limites réseau, sortie et cache.

L'option globale `--verbose` doit précéder la sous-commande :

```bash
python -m hscoach --verbose analyser chemin/partie.hsreplay
```

Le fallback anglais reste volontaire et propre à une analyse :

```bash
python -m hscoach analyser chemin/partie.hsreplay --allow-en-fallback
```

## Modèle temporel V2

Un `turn` représente le demi-tour d'un seul côté. Les snapshots ne sont pas des
synonymes : ils correspondent à des frontières `GameTag.STEP` précises.

```text
MAIN_READY       MAIN_ACTION          MAIN_END             MAIN_CLEANUP
     │                │                   │                      │
turn_start   action_phase_start   action_phase_end          turn_end
     └─ pioche et triggers ─┘      └─ triggers de fin ────────┘
```

- `turn_start_state` : début protocolaire du demi-tour à `MAIN_READY`, avant la pioche
  et les déclenchements de début de tour observés ensuite ;
- `action_phase_start_state` : état à `MAIN_ACTION`, au moment où les décisions de jeu
  deviennent disponibles ;
- `action_phase_end_state` : état à `MAIN_END`, après les actions du joueur mais avant
  les déclenchements de fin de tour ;
- `turn_end_state` : état à `MAIN_CLEANUP`, après ces déclenchements.

Les captures complètes ne sont faites qu'à ces frontières, pas après chaque
micro-paquet. Si `MAIN_CLEANUP` manque, `MAIN_NEXT` peut servir de repli explicite. Un
état à `FINAL_WRAPUP` n'est utilisé comme fin de tour que si `MAIN_END` a été observé ;
une concession depuis la phase d'action ne fabrique donc pas une fausse fin de tour.
Toute frontière réellement indisponible vaut `null` dans les JSON et produit un
avertissement précis.

Les propriétés Python `start_state` et `end_state` restent des alias de lecture pour
les consommateurs V1. Elles désignent respectivement le début et la fin de la phase
d'action ; elles ne réintroduisent pas l'ancien schéma JSON.

## Deltas, actions, choix et options

Pour chacun des trois intervalles entre snapshots, le moteur de deltas compare les
faits visibles :

- changements d'entités avec valeur `before`, `after` et différence numérique ;
- santé, armure et attaque des héros ;
- mana disponible ou utilisé ;
- mouvements entre main et plateau connus.

Un intervalle reste présent avec `complete: false` si l'une de ses frontières manque.
Les deltas atomiques conservent une séquence déterministe et leur phase. Une carte
source n'est associée que si le protocole fournit une cible et une source explicites ;
l'outil n'invente jamais une causalité.

La chronologie classe, lorsque les paquets le permettent, les pioches, cartes jouées,
sorts, invocations, attaques, dégâts, soins, morts, améliorations, affaiblissements,
silences, créations, transformations, mélanges, secrets, fatigue et choix. Un paquet
non compris reste un événement non classifié avec ses métadonnées techniques utiles,
sans copie du XML brut.

Les `Choices` non-mulligan sont conservés avec les cartes proposées et choisies. Une
Découverte n'est nommée ainsi que lorsque la mécanique `DISCOVER` est explicite. Les
`Options` distinguent option disponible, indisponible et choisie. Leur erreur brute
reste dans le JSON complet ; le Markdown masque notamment le marqueur technique
`END_TURN error=INVALID` pour ne pas suggérer une décision inexistante.

## Mulligan

Le mulligan porte un statut indépendant des listes :

- `known` : les catégories sont établies ; une liste vide signifie bien « aucune » ;
- `partial` : seule une partie est déterminable et un avertissement expose l'ambiguïté ;
- `unknown` : les données manquent et les catégories inconnues valent `null`.

Le Markdown rend donc une liste vide par `Aucune.` et une valeur `null` par
`Non déterminé.`. Il ne déduit pas les cartes renvoyées d'un simple nom de paquet.

## Rapports produits

### `game_summary.md`

Le rapport lisible présente le résultat, les classes, le deck joueur connu, le
mulligan, le démarrage protocolaire et les effets de gameplay distincts. Chaque
demi-tour sépare :

1. le début du demi-tour ;
2. l'état « Au moment de décider » ;
3. les décisions enregistrées et les actions effectuées ;
4. les changements observés ;
5. la fin de phase d'action ;
6. l'état après les déclenchements de fin de tour.

Les statistiques affichées sont celles reconstruites à l'instant concerné. Les
répétitions de protocole ne sont pas présentées comme plusieurs effets de gameplay ;
seuls de vrais événements de gameplay identiques peuvent être regroupés.

### `game_analysis.json` — schéma `2.0`

Le JSON exhaustif est déterministe et commence par :

```json
{
  "schema_version": "2.0"
}
```

Ses clés racine stables sont :

```text
schema_version
game
player
opponent
mulligan
start_of_game_events
turns
important_events
unresolved_cards
warnings
diagnostics
```

Chaque demi-tour contient les quatre snapshots, les actions, décisions, choix,
`entity_deltas` et `state_deltas`. Les erreurs d'option et les métadonnées de
diagnostic y restent disponibles. Le passage de l'ancien couple ambigu
`start_state`/`end_state` à quatre frontières change la structure publique : il
justifie le passage SemVer du paquet à `2.0.0` et du schéma de `1.0` à `2.0`.

### `game_llm.json` — schéma `hscoach-llm/1.0`

Ce troisième export est factuel mais plus compact. Il utilise son propre identifiant :

```json
{
  "schema_version": "hscoach-llm/1.0"
}
```

Les informations statiques sont centralisées dans `cards.definitions`, les occurrences
dans `cards.entities`, puis référencées depuis `player_deck`, `mulligan`,
`start_of_game_events` et `turns`. L'état complet au moment de décider est conservé ;
les frontières suivantes peuvent être représentées comme l'application des
`state_changes` plutôt que par une nouvelle copie du board. `important_events`
référence les séquences déjà présentes au lieu de dupliquer les événements. La racine
inclut aussi `game` et `warnings`. Cette déduplication réduit fortement la taille sans
retirer les décisions, les actions ou les limites connues ; elle ne constitue pas un
résumé stratégique.

Les deux schémas JSON sont versionnés indépendamment. Toute évolution incompatible de
l'un doit modifier son identifiant, ses tests de contrat et cette documentation.

## Confidentialité et information cachée

Les rapports partageables passent tous par la même garde de confidentialité :

- les côtés sont `JOUEUR`/`ADVERSAIRE` dans le Markdown et
  `PLAYER`/`OPPONENT` dans les JSON ;
- BattleTags, noms de compte, `accountHi`, `accountLo`, credentials, tokens et
  signatures ne sont pas exportés ;
- les motifs d'URL signée sont recherchés dans chacun des trois rapports avant
  écriture ;
- le deck adverse n'est jamais extrapolé à partir d'un archétype ;
- une carte adverse cachée reste inconnue au moment concerné, même si son identité est
  révélée plus tard ;
- un `game-id` hostile ne peut pas sortir du dossier `output/`.

Le XML d'entrée est non fiable : taille limitée, racine `HSReplay` vérifiée, DTD et
déclarations d'entités refusées avant le parsing officiel. L'anonymisation protège les
rapports générés, pas le replay brut lui-même.

Les replays placés dans `samples/` sont donc privés et locaux. `.gitignore` ignore tout
le contenu de ce dossier sauf `samples/.gitkeep`. Ne forcez jamais leur ajout à Git.
`.cache/` et `output/` sont également ignorés.

## Cache HearthstoneJSON

Le fichier complet `cards.json` frFR, qui comprend aussi héros, pouvoirs, jetons et
enchantements, est conservé ici :

```text
.cache/hearthstonejson/frFR/cards.json
.cache/hearthstonejson/frFR/metadata.json
```

`metadata.json` contient l'empreinte SHA-256 attendue. Elle est vérifiée à chaque
chargement avant de parser le cache. Un cache absent, illisible ou dont l'empreinte ne
correspond pas est considéré comme corrompu : l'outil tente une actualisation, puis
retourne une erreur claire si le réseau est indisponible. Lors d'une actualisation
explicitement demandée, un ancien cache n'est conservé comme repli que s'il est lui-même
valide. Les écritures sont atomiques.

## Diagnostics

Le document complet expose notamment les classes joueur/adversaire, les nombres de
demi-tours, actions, deltas, améliorations, dégâts, soins, cartes créées, options,
cartes inconnues et actions non classifiées, ainsi que le statut du mulligan et la
complétude des quatre frontières. Ces compteurs sont factuels : il n'existe pas de
score global de qualité de reconstruction.

## Limites

- Le replay décrit uniquement ce qui s'est produit ; aucune alternative n'est simulée.
- Les options enregistrées par le client ne représentent pas toutes les lignes
  stratégiques possibles.
- Les cartes adverses cachées, causalités implicites et frontières absentes restent
  inconnues.
- Certaines interactions nouvelles ou complexes peuvent rester non classifiées.
- L'outil ne lit pas `Power.log`, n'analyse pas un dossier en continu, ne fournit ni
  interface graphique ni service web.
- Une URL XML directe est acceptée ; une page publique HSReplay ne l'est pas et aucun
  scraping fragile n'est implémenté.

## Architecture

```text
src/hscoach/
├── cli.py                 CLI et menu français
├── config.py              valeurs par défaut
├── privacy.py             anonymisation et garde avant export
├── input/                 fichiers, HTTP(S) et prévalidation XML
├── cards/                 HearthstoneJSON, cache et résolution frFR
├── replay/                parsing, phases, deltas, timeline, choix et mulligan
├── models/                contrats de données V2
└── output/                Markdown, JSON complet et JSON compact LLM

tests/                     tests unitaires, sécurité et contrats d'export
samples/                   replays utilisateur privés et optionnels
output/                    rapports locaux ignorés par Git
.cache/                    données HearthstoneJSON ignorées par Git
```

## Tests et qualité

La suite principale ne dépend d'aucun replay utilisateur. Elle utilise de petites
entrées anonymisées et couvre notamment les quatre frontières, les deltas, le
mulligan, les options, les choix, le cache SHA-256, la confidentialité et les trois
exports. Les tests d'intégration qui exploitent
`samples/sample_replay.hsreplay` sont optionnels et sont ignorés automatiquement si le
fichier local est absent.

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Pour vérifier réellement l'indépendance vis-à-vis des données privées, exécutez aussi
la suite depuis un clone ne contenant que les fichiers suivis par Git. N'ajoutez jamais
un replay réel à une fixture versionnée ; réduisez et anonymisez un cas minimal.

## Dépannage rapide

- Cache invalide hors ligne : lancez `python -m hscoach actualiser-cartes` lorsque le
  réseau est disponible.
- URL 401/403 : l'URL XML signée a probablement expiré ; obtenez-en une nouvelle sans
  la copier dans un ticket ou un log.
- Replay invalide : vérifiez qu'il s'agit du XML HSReplay, pas d'une page HTML.
- Carte inconnue : actualisez le cache frFR ; utilisez le fallback anglais seulement
  si vous l'acceptez explicitement.
- État `null` : consultez `warnings` ; l'outil préfère signaler une frontière absente
  plutôt que copier l'état d'un autre instant.
