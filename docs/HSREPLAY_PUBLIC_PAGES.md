# Audit des pages publiques HSReplay.net

Audit effectué le 25 août 2026. Son objectif est limité : déterminer s’il existe une
méthode publique et suffisamment stable pour convertir
`https://hsreplay.net/replay/<ID>` en XML brut. Aucun endpoint privé n’a été sondé et
aucun mécanisme de scraping n’a été implémenté.

## 1. API officielle et documentée

Le premier point de contrôle est la
[documentation officielle de l’API HSReplay.net](https://github.com/HearthSim/hsreplaynet-api-docs).
La documentation publiée couvre l’authentification OAuth, le compte et la collection.
Elle ne décrit pas d’opération permettant à une application tierce de résoudre
l’identifiant d’une page publique arbitraire vers son replay XML.

Les clés OAuth sont accordées au cas par cas et les scopes publiés ne constituent pas
un contrat d’accès aux replays publics. Une intégration OAuth ne doit donc pas être
présentée comme une solution à ce besoin tant qu’un endpoint et son autorisation ne sont
pas officiellement documentés.

## 2. Endpoint public stable

Aucun endpoint public stable de résolution n’est documenté dans l’API officielle. La
page `/replay/<ID>` est une page destinée au navigateur, pas un contrat d’API. Le fait
qu’une réponse, un redirect ou un fragment de page soit observable à un instant donné
ne garantit ni sa disponibilité automatisée, ni son format, ni son autorisation
d’usage. HSCoach ne dépend donc d’aucune URL interne découverte empiriquement.

## 3. Donnée structurée prévue pour être consommée

Aucun schéma embarqué dans la page n’est officiellement présenté comme une interface
publique stable permettant d’obtenir le XML. Un état JavaScript, un attribut HTML ou
une requête interne du site ne serait pas un contrat consommateur et peut changer sans
préavis. HSCoach ne les analyse pas.

Les bibliothèques installées ne fournissent pas non plus ce resolver :

- `hsreplay==1.16.2` expose notamment `HSReplayDocument.from_xml_file()` et
  `to_packet_tree()` pour parser un XML déjà obtenu ;
- `hslog==1.20.0` et `hearthstone==9.20.10` fournissent les paquets, entités et enums ;
- `httpx` sert uniquement à télécharger une URL HTTP/HTTPS qui pointe directement vers
  un XML validé.

## 4. Mécanisme web en dernier recours

Un scraper HTML serait fragile et incompatible avec la politique du projet. Les
[conditions d’utilisation officielles de HearthSim](https://hearthsim.net/legal/terms-of-service.html)
encadrent l’accès automatisé et le scraping des services hors API. Il n’est pas
acceptable de contourner cette frontière, un refus HTTP ou une protection du site.

## Décision V3

`HsReplayPageSource` reconnaît syntaxiquement une page officielle, mais sa méthode de
chargement s'arrête localement, sans requête réseau, avec le message :

> Les liens de page HSReplay ne sont pas pris en charge directement.
>
> Pour récupérer le lien direct :
> 1. Ouvrez le replay sur hsreplay.net dans votre navigateur.
> 2. Ouvrez les outils de développement (F12) puis l'onglet Réseau.
> 3. Rechargez la page et filtrez sur ".xml".
> 4. Copiez l'URL du fichier .hsreplay.xml qui apparaît.
>
> Utilisez ensuite ce lien direct, ou téléchargez le fichier et utilisez-le en local.

Le moteur continue de prendre en charge un fichier local ou une URL directe vers le
XML, y compris une URL S3 signée dont les paramètres restent masqués. Ces URL directes
sont temporaires et expirent typiquement après une heure : elles ne constituent donc
pas un identifiant durable et ne sont jamais persistées par HSCoach. Le resolver
séparé permettra une évolution si HSReplay.net publie une API adaptée ou donne une
autorisation explicite.

## Contrats de test

La suite V3 doit prouver que :

- `hsreplay.net/replay/<ID>` et `www.hsreplay.net/replay/<ID>` sont reconnus comme
  `HsReplayPageSource` ;
- l'appel à `load()` rend le message français et les quatre étapes ci-dessus sans
  appeler le client HTTP ;
- un domaine ressemblant à `hsreplay.net.example` n’est jamais classé comme page
  officielle ;
- les URL XML directes utilisent un client mocké, valident le statut, la taille et la
  racine `HSReplay` ;
- une page HTML reçue avec HTTP 200 est refusée comme non-XML ;
- aucune query string signée n’apparaît dans les labels, logs, erreurs ou réglages.

Avant toute future implémentation, cet audit devra être refait contre la documentation
officielle alors en vigueur. L’existence d’une technique non documentée ne suffira pas.
