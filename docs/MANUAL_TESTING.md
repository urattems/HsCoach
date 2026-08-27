# Validation manuelle de HSCoach V3

Cette checklist complète les tests automatisés. Elle ne doit jamais contenir de replay
privé, d’URL S3 réelle, de BattleTag ou de credential. Conservez les valeurs de test
sensibles uniquement dans votre session locale.

## 1. Préparation

Sur Windows 11 64 bits, avec Python 3.11 ou plus récent :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,gui,build]"
python -m pip check
```

Utilisez une copie privée d’un replay court, un replay long et cinq replays pour le
lot. Ne les placez pas dans `tests/fixtures/`. Si `samples/sample_replay.hsreplay`
existe, calculez son empreinte avant et après les essais :

```powershell
Get-FileHash .\samples\sample_replay.hsreplay -Algorithm SHA256
```

L’empreinte finale doit être identique.

## 2. Validation automatisée

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m hscoach.gui --smoke-test
```

Critères : zéro échec, zéro erreur Ruff et sortie 0 du smoke test. Les seules
désactivations acceptables sont les tests explicitement marqués comme dépendant d’un
sample privé absent.

## 3. Matrice des sources

| Cas | Action | Résultat attendu |
|---|---|---|
| `.hsreplay` valide | Déposer un fichier court. | Source ajoutée, puis rapports demandés générés. |
| `.xml` valide | Utiliser **Parcourir...**. | Même résultat qu’un `.hsreplay`. |
| `.txt` contenant le XML | Déposer le fichier. | Accepté après validation du contenu. |
| Mauvaise extension | Déposer un `.zip` ou `.exe`. | Refus français immédiat ; rien n’est exécuté ou extrait. |
| Faux XML | Déposer un texte quelconque renommé `.xml`. | « Le replay ne contient pas un document XML valide. » |
| Mauvaise racine | Fournir un XML dont la racine n’est pas `HSReplay`. | Replay refusé, aucun rapport partiel. |
| DOCTYPE HSReplay externe | Fournir un XML avec le DOCTYPE officiel sans sous-ensemble interne. | Accepté sans téléchargement ni résolution du DTD. |
| DTD/XXE | Fournir une fixture avec sous-ensemble interne ou `ENTITY`. | Refus avant parsing ; aucune résolution externe. |
| XML direct valide | Coller une URL HTTPS de test pointant vers le XML. | Téléchargement, validation, analyse. |
| XML signé valide | Coller localement une URL S3 signée. | Succès ; ni la liste, ni le log, ni les réglages ne contiennent la query string. |
| URL signée expirée | Utiliser une URL de test expirée. | « Le lien XML a expiré. » ou refus HTTP français équivalent. |
| HTTP 403 | Simuler un serveur de test répondant 403. | Erreur française contrôlée, sans traceback. |
| HTTP 404 | Simuler un serveur de test répondant 404. | « Replay distant introuvable » ou formulation française équivalente. |
| HTML avec HTTP 200 | Pointer vers une page HTML de test. | Refus : la réponse n’est pas un replay HSReplay XML. |
| URL non HTTP(S) | Coller `file:`, `ftp:` ou une URL avec identifiants. | Refus avant toute requête réseau. |
| Très gros fichier | Dépasser la limite configurée de 50 Mio. | Arrêt de lecture/téléchargement et erreur de taille. |
| Page HSReplay publique, V3 | Coller `https://hsreplay.net/replay/ID_DE_TEST`. | Aucun scraping ni téléchargement ; message donnant les quatre étapes F12/Réseau pour copier le XML direct. |
| Page HSReplay, futur resolver officiel | À exécuter uniquement si une API officielle est un jour activée. | Test dédié et documenté ; ce cas est **non applicable en V3**. |

Pour les URL distantes, utilisez un serveur HTTP contrôlé ou les mocks de la suite.
Ne commitez jamais l’URL signée utilisée lors d’un essai manuel.

## 4. Parcours GUI

Lancez :

```powershell
python -m hscoach.gui
```

Vérifiez dans cet ordre :

1. La fenêtre s’ouvre sans console parasite et porte le titre
   **Hearthstone Replay Analyzer**.
2. Le bouton **ANALYSER** est désactivé sans source ou avec un dossier invalide.
3. Un fichier peut être ajouté par glisser-déposer et par **Parcourir...**.
4. Plusieurs fichiers déposés ensemble produisent plusieurs lignes, sans doublon
   involontaire.
5. Une source peut être supprimée avant l’analyse.
6. Le libellé d’une URL signée montre au plus l’hôte et un chemin masqué, jamais la
   query string.
7. Le sélecteur **Choisir...** accepte un dossier accessible en écriture et refuse un
   fichier ou un emplacement inaccessible.
8. Au premier lancement, la sortie proposée est `Documents/HSCoach`.
9. Markdown et JSON pour IA sont cochés ; JSON complet ne l’est pas.
10. Pendant une analyse longue, la fenêtre reste déplaçable, redimensionnable et
    réactive. La progression ne fabrique pas de pourcentage.
11. Le résultat affiche les classes, le résultat de partie et les boutons disponibles.
12. **Voir le résumé** et **Ouvrir le dossier** utilisent l’application système.
13. Une erreur normale reste en français et n’affiche aucune traceback.

### Persistance

Fermez puis relancez l’application. Vérifiez que sont conservés uniquement :

- dossier de sortie ;
- trois choix d’export ;
- ouverture du dossier après analyse ;
- géométrie de fenêtre, si activée.

Vérifiez qu’aucune ancienne source, URL, query string, carte ou identité ne réapparaît.

### Lot et annulation

Ajoutez cinq sources dont la troisième est volontairement invalide. Résultat attendu :

```text
4 réussies
1 erreur
```

La quatrième et la cinquième doivent être traitées malgré l’échec. Relancez un lot long,
cliquez **Annuler**, puis vérifiez que les travaux encore en attente ne commencent pas,
que les rapports déjà terminés restent valides et qu’aucun temporaire de téléchargement
ne subsiste. Un parseur déjà en cours peut finir avant l’arrêt effectif.

### Accessibilité Windows

- Parcourez tous les contrôles avec Tab et Maj+Tab.
- Actionnez les boutons principaux avec le clavier.
- Testez le thème système clair et sombre.
- Testez l’affichage Windows à 100 %, 125 % et 150 %.
- Réduisez puis agrandissez la fenêtre : aucun contrôle essentiel ne doit devenir
  inaccessible et les textes ne doivent pas être tronqués de manière ambiguë.

## 5. Cartes et fonctionnement hors ligne

Avec un cache frFR valide, désactivez temporairement le réseau puis analysez un replay :
le résultat doit être identique. Sans cache, le premier lancement hors ligne doit
expliquer que les données françaises doivent être téléchargées une première fois, sans
crash ni fallback anglais.

Corrompez uniquement une **copie de test** de `cards.json`. La GUI doit signaler un
cache corrompu et proposer/tenter une actualisation. Ne modifiez pas le cache utilisé
pour les validations de référence.

## 6. Validation des rapports

Ouvrez réellement `game_summary.md` et `game_llm.json`. Cherchez chaque occurrence de :

```powershell
rg -n "Carte inconnue|Non déterminé|Événement non classifié|Dormant|Beatrix|créé|créée" <dossier-de-sortie>
```

Pour chaque résultat, vérifiez le contexte dans le replay :

- `CREATOR` seul est une provenance, pas une création à cet instant ;
- un passage Dormant ne produit pas un faux `DEBUFF` suivi d’un faux `BUFF` ;
- un vrai changement de statistiques reste visible ;
- Beatrix n’est présentée qu’une fois au niveau gameplay lorsque plusieurs paquets
  décrivent le même déclenchement ;
- les occurrences protocolaires restent disponibles dans le JSON complet ;
- les enchantements techniques ne polluent ni le résumé ni `important_events` ;
- une carte adverse cachée n’est jamais révélée rétroactivement.

Scannez ensuite les sorties partageables :

```powershell
rg -n "accountHi|accountLo|BattleTag|X-Amz-Credential|X-Amz-Security-Token|X-Amz-Signature" <dossier-de-sortie>
```

La commande ne doit produire aucune ligne. Contrôlez aussi manuellement les noms réels
présents dans les replays privés, sans les copier dans un rapport de test.

## 7. Build Windows

Depuis une session PowerShell standard, sans élévation :

```powershell
.\scripts\build_windows.ps1
Test-Path .\dist\HSCoach\HSCoach.exe
.\dist\HSCoach\HSCoach.exe --smoke-test
```

Le script doit échouer clairement si les extras GUI/build ne sont pas installés. Le
dossier final doit contenir `HSCoach.exe`, `LICENSE`, les notices tierces et les
dépendances one-folder, mais aucun `samples/`, replay, rapport, `.cache/` ou `.venv/`.

### Gate licences Qt/PySide6

Avant une publication binaire, vérifiez que `licenses/GPL-3.0-only.txt`,
`licenses/LGPL-3.0-only.txt` et `THIRD_PARTY_NOTICES.md` sont présents dans le bundle.
Le simple fait qu’un wheel PySide6 fournisse un fichier
`LicenseRef-Qt-Commercial.txt` ne démontre pas la conformité à l’option LGPL.

Inventoriez les DLL et plugins Qt réellement embarqués, leurs composants tiers et leurs
notices. Documentez l’accès aux sources correspondantes ainsi que la possibilité de
remplacer les bibliothèques dynamiques. Tant que ce relevé n’a pas été revu pour la
version exacte du bundle, **la publication publique du binaire reste bloquée**. Cette
checklist et les textes joints ne constituent pas un avis juridique.

Copiez le dossier `dist/HSCoach` vers un chemin contenant des espaces et des accents,
puis relancez le smoke test. Lancez ensuite l’interface par double-clic, analysez un
replay et ouvrez le résultat. Vérifiez également sur un compte Windows standard.

L’exécutable peut être non signé :

```powershell
Get-AuthenticodeSignature .\dist\HSCoach\HSCoach.exe
Get-FileHash .\dist\HSCoach\HSCoach.exe -Algorithm SHA256
```

Consignez le statut de signature et le SHA-256 dans les notes de Release. Ne présentez
jamais un binaire non signé comme signé.

## 8. Validation depuis un état Git propre

Une publication ne doit pas être créée en zippant le dossier de travail, qui contient
potentiellement des replays ignorés. Utilisez une archive Git ou un clone neuf.

Exemple non destructif :

```powershell
$auditRoot = Join-Path ([IO.Path]::GetTempPath()) ("hscoach-clean-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $auditRoot | Out-Null
git archive --format=tar HEAD -o (Join-Path $auditRoot "source.tar")
New-Item -ItemType Directory -Path (Join-Path $auditRoot "source") | Out-Null
tar -xf (Join-Path $auditRoot "source.tar") -C (Join-Path $auditRoot "source")
```

Dans `source`, créez un venv neuf, installez `.[dev,gui,build]`, puis exécutez Pytest,
Ruff, le smoke test GUI et le build Windows. Vérifiez avec `git ls-files` et le contenu
de l’archive qu’aucun replay privé n’est présent.

## 9. Critères de validation finale

- zéro échec automatisé ;
- CLI et GUI utilisent le même `AnalysisService` ;
- replay court, replay long et lot de cinq terminent sans gel d’interface ;
- les trois formats suivent exactement les cases cochées ;
- toutes les erreurs utilisateur sont françaises ;
- aucune donnée sensible dans les rapports, labels, logs ou réglages ;
- replay original inchangé ;
- clean clone vert et buildable ;
- `dist/HSCoach/HSCoach.exe` fonctionne sans Python et sans droit administrateur ;
- limites HSReplay public, annulation et signature de code documentées honnêtement.
