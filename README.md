# Hearthstone Replay Analyzer

Hearthstone Replay Analyzer (`hscoach`) transforme un replay Hearthstone brut au
format HSReplay XML en deux rapports factuels, structurés et anonymisés : un résumé
Markdown lisible et un document JSON destiné à un LLM ou à un futur programme.

L'outil répond à la question « que s'est-il réellement passé dans cette partie ? ».
Il reconstruit prudemment le deck du joueur, le mulligan, la chronologie, les états de
main et de plateau connus, les options enregistrées et la provenance de certaines
cartes générées. Il ne note pas les plays, ne recommande aucun choix et ne simule pas
de ligne alternative.

La locale par défaut est `frFR`. Aucun nom anglais n'est utilisé silencieusement : une
carte non résolue devient `Carte inconnue [CARD_ID]` et produit un avertissement.

## Prérequis

- Python 3.11 ou une version plus récente ;
- un accès internet lors du premier chargement des données HearthstoneJSON, sauf si
  un cache valide existe déjà ;
- un replay local HSReplay ou une URL HTTP/HTTPS pointant directement vers son XML.

Les dépendances principales sont volontairement limitées :

- `hsreplay` pour parser le format officiel HSReplay ;
- `hslog` et `hearthstone` pour les paquets, les états et les enums HearthSim ;
- `httpx` pour les téléchargements en streaming ;
- `pytest` et `ruff` dans le groupe de développement.

## Installation

Placez-vous à la racine du dépôt avant d'exécuter les commandes suivantes. Une
installation éditable permet d'utiliser aussi bien `python -m hscoach` que la commande
courte `hscoach` tant que l'environnement virtuel est actif.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Si PowerShell bloque l'activation des scripts, autorisez-la uniquement pour le
processus courant, puis réessayez :

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Il est également possible de ne pas activer l'environnement et d'appeler directement
son interpréteur :

```powershell
.\.venv\Scripts\python.exe -m hscoach --help
```

### Linux et macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Pour une installation sans les outils de test et de lint, remplacez la dernière
commande par `python -m pip install -e .`.

## Utilisation

### Analyser un fichier local

```bash
python -m hscoach analyser samples/sample_replay.hsreplay
```

Les extensions `.hsreplay`, `.xml` et `.txt` sont acceptées. Un fichier sans extension
est également accepté, ce qui permet d'utiliser les replays exportés sans suffixe. Le
contenu reste systématiquement validé comme un document HSReplay XML ; renommer un
autre fichier ne suffit donc pas à le rendre acceptable.

Une analyse réussie crée :

```text
output/<game-id>/game_summary.md
output/<game-id>/game_analysis.json
```

Le dossier `<game-id>` est neutralisé si l'identifiant du replay ne peut pas être
utilisé comme un nom de dossier sûr.

### Analyser une URL XML directe

```bash
python -m hscoach analyser "https://example.com/replay.hsreplay.xml?X-Amz-Signature=..."
```

L'URL doit pointer directement vers le replay XML. Une page publique telle que
`https://hsreplay.net/replay/<ID>` n'est pas prise en charge en V1. Seuls HTTP et HTTPS
sont autorisés. Le téléchargement est effectué en streaming avec un timeout de
20 secondes et une limite de 50 MiB par défaut.

Les paramètres d'une URL signée, notamment `X-Amz-Credential`,
`X-Amz-Security-Token` et `X-Amz-Signature`, ne sont jamais affichés dans les logs ni
recopiés dans les rapports.

### Utiliser le menu interactif

```bash
python -m hscoach
```

Le menu en français permet d'analyser un fichier, d'analyser une URL XML directe,
d'actualiser les cartes, d'afficher la configuration ou de quitter. Le programme
exécute le choix puis rend la main au terminal.

### Inspecter un replay sans produire de rapport

```bash
python -m hscoach inspecter samples/sample_replay.hsreplay
```

La commande affiche notamment la validité du replay, le build Hearthstone, le nombre
d'entités, d'événements et de demi-tours, les Card IDs résolus ou inconnus, ainsi que
la présence du deck, du mulligan et des options. Un « demi-tour » correspond au tour
d'un seul côté ; le nombre de tours Hearthstone complets est donc une mesure distincte.

### Actualiser les données des cartes

```bash
python -m hscoach actualiser-cartes
```

La source utilisée par défaut est le `cards.json` complet de HearthstoneJSON en
`frFR`, et non le seul sous-ensemble des cartes collectionnables. Le cache est stocké
ici :

```text
.cache/hearthstonejson/frFR/cards.json
.cache/hearthstonejson/frFR/metadata.json
```

Un cache valide est utilisé immédiatement. Si une actualisation échoue, le cache
existant est conservé. Sans réseau et sans cache valide, la CLI retourne une erreur
explicite en français.

La locale à télécharger peut être précisée, même si `frFR` reste la valeur attendue
pour l'usage normal :

```bash
python -m hscoach actualiser-cartes --locale frFR
```

### Afficher la configuration

```bash
python -m hscoach configuration
```

Les valeurs V1 sont centralisées dans `AppConfig` : locale `frFR`, anonymisation
active, taille maximale 50 MiB, timeout HTTP 20 secondes, sorties dans `output/` et
cache dans `.cache/`. Ces chemins sont relatifs au dossier depuis lequel la commande
est lancée.

### Diagnostics détaillés et fallback anglais explicite

L'option globale `--verbose` doit être placée avant la sous-commande :

```bash
python -m hscoach --verbose analyser replay.hsreplay
```

Par défaut, une traduction française manquante reste inconnue. Le fallback anglais ne
peut être activé que volontairement pour la commande d'analyse :

```bash
python -m hscoach analyser replay.hsreplay --allow-en-fallback
```

## Rapports produits

Les deux fichiers sont encodés en UTF-8 et écrits de façon atomique. Relancer
l'analyse du même `game-id` remplace les rapports correspondants.

### `game_summary.md`

Le Markdown privilégie une lecture compacte par un humain ou un LLM. Il contient :

- le résumé de la partie, les classes, le résultat, le format et la durée connue ;
- le deck du joueur groupé par coût, sous les noms français disponibles ;
- la main proposée au mulligan, les cartes conservées, renvoyées et reçues ;
- les effets distincts observés au début de la partie ;
- chaque demi-tour avec mana, héros, main connue, plateau, actions et décisions ;
- les statistiques actuelles reconstruites des serviteurs, et non seulement leurs
  statistiques imprimées ;
- les événements importants, informations inconnues et avertissements du parseur.

Le deck adverse n'est jamais extrapolé à partir d'un archétype. Il reste indiqué comme
inconnu lorsqu'il n'est pas explicitement présent dans le replay.

### `game_analysis.json`

Le JSON est déterministe, utilise des clés techniques stables en anglais et commence
par :

```json
{
  "schema_version": "1.0"
}
```

Sa structure racine est la suivante :

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

`turns` contient un élément par demi-tour avec son numéro de manche, le côté actif,
l'état de début, les actions, les décisions enregistrées et l'état de fin. Les cartes
et événements conservent, lorsque pertinent, leur visibilité (`known` ou `hidden`) et
leur source d'information (`replay_explicit`, `gamestate_reconstructed` ou
`uncertain`). Les détails techniques nécessaires au diagnostic restent dans le JSON,
sans inclure le XML brut ni la source d'entrée.

Le schéma `1.0` est défini par les dataclasses de `src/hscoach/models/` et le mapping
public de `src/hscoach/output/json_export.py`. Toute évolution incompatible devra
changer `schema_version`.

## Confidentialité et sécurité

Les rapports sont conçus pour être partageables et l'anonymisation est toujours active
dans la CLI V1 :

- le Markdown présente les deux côtés comme `JOUEUR` et `ADVERSAIRE`, tandis que le
  JSON conserve les valeurs techniques anonymes `PLAYER` et `OPPONENT` ;
- les BattleTags, noms de compte, `accountHi` et `accountLo` ne sont pas exportés ;
- une URL signée complète n'est ni loggée ni conservée ;
- une dernière barrière refuse l'écriture d'un rapport contenant encore un marqueur
  sensible connu ;
- un identifiant de partie hostile ne peut pas provoquer de path traversal ;
- `.cache/` et `output/` sont ignorés par Git.

Les entrées sont traitées comme non fiables. La prévalidation limite leur taille,
vérifie la racine `HSReplay` et rejette notamment les DTD et déclarations d'entités
avant de transmettre le contenu à `python-hsreplay`. Elle n'exécute aucun contenu et
ne résout pas d'entité externe.

Les fichiers de replay bruts peuvent néanmoins contenir des identifiants personnels.
Ne les publiez pas sans les avoir examinés ; l'anonymisation porte sur les rapports
générés, pas sur le fichier source.

## Limites importantes

- Le replay décrit ce qui s'est réellement produit. L'outil ne simule pas ce qui se
  serait produit après un autre choix.
- Les cartes adverses cachées restent inconnues. Une identité révélée plus tard n'est
  pas appliquée rétroactivement aux états antérieurs.
- `Options` et `SendOption` décrivent uniquement les propositions enregistrées par le
  client à un instant donné. Ils ne constituent pas un moteur stratégique et ne
  représentent pas nécessairement toutes les séquences de jeu possibles.
- Le programme n'évalue pas la qualité d'un play, ne donne pas de score et ne propose
  pas de « meilleur coup ».
- Certaines interactions Hearthstone complexes, données absentes ou versions futures
  du protocole peuvent empêcher une reconstruction parfaite. L'outil préfère alors
  une carte inconnue, un événement non classifié ou un avertissement à une information
  inventée.
- Le mulligan est classé à partir des changements de zone réellement observés. Lorsqu'une
  distinction n'est pas suffisamment établie, le rapport indique une reconstruction
  partielle.
- Les statistiques, la main, le plateau, l'arme et le pouvoir héroïque ne sont affichés
  que dans la mesure où l'état HearthSim permet de les reconstruire au moment concerné.
- La V1 accepte une URL XML directe, mais ne scrape pas les pages HSReplay, ne lit pas
  `Power.log`, n'analyse pas automatiquement un dossier et ne fournit ni GUI ni service
  web.

## Dépannage

### `python` ou `py` est introuvable

Installez Python 3.11 ou plus récent, puis vérifiez la version avec
`python --version`. Sous Windows, le lanceur `py -3.11` peut être disponible même si
la commande `python` ne l'est pas encore.

### L'activation PowerShell est refusée

Utilisez la commande `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
montrée dans la section d'installation. Elle ne modifie pas durablement la politique
de la machine. Vous pouvez aussi appeler `.\.venv\Scripts\python.exe` directement.

### Le téléchargement des cartes échoue

Vérifiez la connexion puis exécutez `python -m hscoach actualiser-cartes`. Un cache
valide reste utilisable hors ligne. Si aucun cache n'existe, une première connexion à
HearthstoneJSON est indispensable.

### Une URL distante retourne 401 ou 403

Les URL S3 signées expirent. Récupérez une nouvelle URL XML directe, sans la copier
dans un ticket, un log ou un rapport public.

### Le replay est déclaré invalide

Vérifiez qu'il s'agit du XML HSReplay lui-même, et non d'une page HTML téléchargée ou
d'une page `hsreplay.net/replay/...`. Les extensions prises en charge sont
`.hsreplay`, `.xml`, `.txt` et l'absence d'extension.

### Des cartes restent inconnues

Actualisez d'abord le cache `frFR`. Si HearthstoneJSON ne fournit réellement pas la
traduction, le comportement normal est de conserver `Carte inconnue [CARD_ID]`. Le
fallback anglais est disponible uniquement via `--allow-en-fallback`.

### Les rapports ne peuvent pas être écrits

Vérifiez les droits d'écriture du dossier courant et l'absence d'un fichier nommé
`output` à la place du dossier attendu. La CLI affiche une erreur en français et
chaque fichier de rapport est remplacé atomiquement.

## Architecture

```text
src/hscoach/
├── cli.py                 interface en ligne de commande et menu français
├── config.py              valeurs par défaut centralisées
├── privacy.py             masquage et barrière avant export
├── input/                 fichiers locaux, URL distantes et validation XML
├── cards/                 cache HearthstoneJSON, localisation et résolution
├── replay/                parsing, mulligan, timeline, options et game state
├── models/                dataclasses stables de l'analyse
└── output/                rendus Markdown et JSON

tests/                     tests unitaires, sécurité et intégration réelle
samples/                   replays locaux fournis par l'utilisateur
output/                    rapports générés, jamais versionnés
.cache/                    données HearthstoneJSON, jamais versionnées
```

Le flux principal reste séparé en étapes : chargement sécurisé, parsing officiel,
résolution frFR, reconstruction factuelle, anonymisation, puis rendu. Cette séparation
évite de mélanger les entrées réseau, la logique de replay et la présentation.

## Tests et qualité

La suite couvre notamment les entrées locales et distantes, la sécurité XML, le cache
offline, la résolution française, le parser réel, le mulligan, la timeline, les états
buffés, les informations cachées, les options, la confidentialité, la CLI et les deux
exports.

Les tests qui utilisent le replay réel attendent le fichier
`samples/sample_replay.hsreplay`. Ce fichier utilisateur ne doit jamais être modifié.

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Roadmap

### V2 — URL de page HSReplay

Accepter directement une URL `https://hsreplay.net/replay/<ID>` et récupérer le replay
si une méthode stable, sûre et acceptable existe. Ce scraping n'est pas implémenté en
V1.

### V3 — replay local automatique

Analyser directement `Power.log` ou le dernier replay produit localement par
Hearthstone Deck Tracker afin de proposer une commande « Analyser ma dernière partie ».
La lecture live et la surveillance de dossiers restent hors scope V1.

### V4 — viewer local

Ajouter une petite interface locale permettant de choisir un tour, voir la main et le
plateau connus, parcourir les actions et exporter uniquement les tours intéressants.
