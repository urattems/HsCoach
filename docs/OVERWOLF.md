# Archive — ancienne réflexion Overwolf

> Ce document est conservé pour l’historique. Overwolf n’est plus une direction active
> de HSCoach et les propositions ci-dessous ne constituent pas une roadmap.

HSCoach V3 n’est pas une application Overwolf. Ce document fixe seulement une frontière
d’architecture afin qu’une façade puisse être ajoutée plus tard sans déplacer la logique
Hearthstone vers JavaScript ou vers Overwolf.

## Décision principale

Une future façade Overwolf sera un client supplémentaire du moteur :

```text
Overwolf Native / OW-Electron UI
                │
                │ requête JSON versionnée
                ▼
adapter / process bridge
                │
                │ lance et surveille un processus
                ▼
hscoach-engine.exe
                │
                ▼
AnalysisService
                │
                ├── sources locales ou XML direct
                ├── parser HearthSim
                ├── reconstruction factuelle
                └── exports anonymisés
```

L’adapter ne connaît ni `GameTag`, ni cartes, ni zones, ni mulligan. Il valide une
requête, lance le moteur, relaie une progression factuelle et lit un résultat structuré.
La GUI PySide6, la CLI et Overwolf doivent rester trois clients du même
`AnalysisService`.

## Aucun Game Events Provider Hearthstone

Il ne faut construire aucune fonction dépendant du Game Events Provider (GEP) pour
Hearthstone. La [liste officielle des jeux GEP dépréciés](https://dev.overwolf.com/ow-native/live-game-data-gep/supported-games/deprecated/overview/)
classe Hearthstone (`9898`) comme retiré à compter du **2026-08-10**, en raison du
manque d’usage et de joueurs. Cette donnée est une contrainte d’architecture, pas une
anomalie temporaire à contourner.

Une éventuelle façade reposera donc uniquement sur les entrées déjà maîtrisées par
HSCoach : replays sélectionnés par l’utilisateur, fichiers locaux, logs explicitement
pris en charge à l’avenir et URL XML directes. Elle ne promettra aucune télémétrie live
Overwolf.

## Pont de processus recommandé

Overwolf documente un
[Process Manager Plugin](https://dev.overwolf.com/ow-native/guides/dev-tools/plugins/the-process-manager-plugin/)
pour lancer un exécutable externe. Cette voie est préférable à l’ajout immédiat d’un
serveur HTTP local.

Interface cible, à versionner avant implémentation :

```text
hscoach-engine.exe machine-analyse --request <request.json> --result <result.json>
```

Alternative possible après prototype : une requête JSON sur `stdin` et un résultat
JSON sur `stdout`. Dans ce mode, `stdout` doit rester exclusivement machine-readable et
les logs doivent aller sur `stderr`. Le mode fichier est plus simple pour un premier
pont Windows, facilite l’écriture atomique et évite les problèmes d’encodage ou de
buffer de pipe.

Exemple conceptuel de requête :

```json
{
  "schema_version": "hscoach-machine-request/1.0",
  "sources": [
    {"kind": "local", "path": "C:/Users/USER/Documents/game.hsreplay"}
  ],
  "output_directory": "C:/Users/USER/Documents/HSCoach",
  "exports": {
    "markdown": true,
    "llm_json": true,
    "full_json": false
  }
}
```

Exemple conceptuel de résultat :

```json
{
  "schema_version": "hscoach-machine-result/1.0",
  "success": true,
  "items": [
    {
      "success": true,
      "reports": {
        "markdown": "C:/Users/USER/Documents/HSCoach/GAME/game_summary.md",
        "llm_json": "C:/Users/USER/Documents/HSCoach/GAME/game_llm.json"
      },
      "warnings": []
    }
  ]
}
```

Ces exemples ne sont pas des commandes publiques V3. Ils documentent la forme la plus
simple à stabiliser si un client Overwolf est réellement développé.

## Cycle de vie envisagé

1. L’UI demande explicitement à l’utilisateur un ou plusieurs replays.
2. L’adapter écrit une requête temporaire avec des permissions utilisateur.
3. Le Process Manager lance `hscoach-engine.exe` sans élévation.
4. Le moteur appelle `AnalysisService`, écrit les rapports puis le résultat de manière
   atomique.
5. L’adapter affiche uniquement les informations prévues par le contrat.
6. Les temporaires de requête/résultat sont supprimés après lecture ou au prochain
   démarrage contrôlé.

Une annulation doit d’abord empêcher les éléments de lot encore en attente. Le moteur
ne doit pas être tué pendant une écriture ; si l’arrêt coopératif d’un parsing en cours
n’est pas disponible, cette limite doit être affichée.

## Sécurité et confidentialité

- Aucun BattleTag, nom de compte, `accountHi` ou `accountLo` dans les messages IPC.
- Aucune query string d’URL signée dans l’UI, les logs ou le fichier résultat.
- Un chemin local reste limité au fichier explicitement choisi par l’utilisateur.
- Les requêtes sont bornées en taille et validées par schéma.
- Les résultats ne peuvent référencer que le dossier de sortie autorisé.
- L’adapter ne transmet jamais le XML brut à un service distant.
- Les logs du moteur restent sur `stderr` ou dans un fichier utilisateur redacted.
- Aucun port local, endpoint HTTP ou exception de pare-feu n’est nécessaire.
- Aucun droit administrateur n’est demandé.

Le moteur et l’adapter devront être distribués et mis à jour ensemble, avec vérification
de version du contrat. Une incompatibilité doit produire une erreur explicite, jamais un
fallback silencieux.

## Ce qui reste hors scope V3

- création et publication d’une application Overwolf ;
- plugin Process Manager ou permissions Overwolf ;
- lecture continue de `Power.log` ;
- overlay en jeu ;
- télémétrie live ;
- serveur Flask/FastAPI/local ;
- authentification HSReplay ou scraping d’une page publique ;
- protocole IPC définitif.

Avant de commencer une intégration, il faudra revérifier la documentation Overwolf,
car ses API et politiques sont susceptibles d’évoluer.
