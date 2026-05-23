# La Matinale

Bespoke morning newspaper — édition personnelle, fraîche à 7 h Londres tous les matins.

## Architecture

| Pièce | Rôle |
|---|---|
| `index.html` | Page statique. Charge `data.json` au démarrage et restitue les 4 cahiers. |
| `data.json` | Contenu éditorial du jour. Régénéré par le cron. |
| `refresh/refresh.py` | Script qui régénère `data.json` à partir des flux RSS + (optionnel) passage éditorial via l'API Claude. |
| `refresh/requirements.txt` | Dépendances Python (`feedparser`, `anthropic`, `yfinance`). |
| `.github/workflows/refresh.yml` | Cron GitHub Actions à 06:00 + 07:00 UTC. Le script ne s'exécute que si l'heure de Londres est 7 h (gestion BST/GMT automatique). |
| `manifest.webmanifest` + `icon.svg` | Métadonnées PWA pour l'ajout à l'écran d'accueil iPad. |

## Hébergement — GitHub Pages

Une fois le repo poussé :

1. **Settings → Pages** → *Source* : `Deploy from a branch` → *Branch* : `main` / `(root)` → Save.
2. Attendre ~1 min, puis l'URL publique apparaît : `https://<user>.github.io/la-matinale/`.

## Activer la couche éditoriale (recommandé)

Sans clé API Anthropic, le script tombe en mode dégradé : agrégat RSS brut, sans ligne *"Pourquoi ça compte"*, sans hiérarchisation, sans lede.

Pour activer la vraie curation :

1. Obtenir une clé sur [console.anthropic.com](https://console.anthropic.com).
2. Dans le repo : **Settings → Secrets and variables → Actions → New repository secret**.
3. Nom : `ANTHROPIC_API_KEY` · Valeur : la clé.

Le prochain cron tournera avec la curation éditoriale activée. Coût estimé : <1 € / jour (4-5 appels Claude Sonnet 4.6 à 7 h).

## Cron — gestion BST/GMT

GitHub Actions n'accepte que des crons UTC. Le workflow programme **deux** déclenchements (06:00 et 07:00 UTC). Le script `refresh.py` vérifie l'heure de Londres en tête : il ne fait rien si l'heure locale n'est pas 7 h. Résultat : **un seul vrai run par jour**, à 07:00 Europe/London, qu'on soit en heure d'été ou d'hiver.

## Tester en local

```bash
# Forcer l'exécution sans la garde horaire
SKIP_HOUR_CHECK=1 python3 refresh/refresh.py

# Avec la couche éditoriale Claude
SKIP_HOUR_CHECK=1 ANTHROPIC_API_KEY=sk-ant-... python3 refresh/refresh.py
```

Puis ouvrir `index.html` dans un navigateur (servir avec un petit serveur HTTP — `python3 -m http.server` — sinon le fetch de `data.json` échouera à cause de `file://`).

## Sources RSS

Configurées dans `refresh/refresh.py` (constante `SOURCES`). Pour en ajouter / remplacer une :

```python
SOURCES["france"].append(("Mon Média", "https://exemple.fr/rss.xml"))
```

Les flux par défaut visent : Le Monde, Les Échos, BBC, Guardian, Reuters, FT, Music Business Worldwide, Synthtopia, KVR, Push Square, Eurogamer. Vérifier périodiquement qu'ils répondent encore (les éditeurs cassent parfois leurs flux RSS sans préavis).

## Sources marchés

- **Indices et movers** : `yfinance` (gratuit, sans clé). Peut occasionnellement échouer — dans ce cas, la valeur de la veille reste affichée.
- **Recommandations analystes (`ratings`)** : reste en *mock* dans `data.json` faute de feed gratuit. Brancher Bloomberg / Refinitiv / Visible Alpha ici si tu as un accès.

## iPad — ajout à l'écran d'accueil

1. Ouvrir l'URL dans Safari (pas Chrome).
2. Bouton **Partager** → **Sur l'écran d'accueil**.
3. L'icône reprend `icon.svg`, le titre court est "Matinale".
4. Ouverte depuis l'icône, la page s'affiche en mode standalone (sans la barre Safari).

## Pourquoi `data.json` et pas du HTML statique régénéré

Pour minimiser les diffs commités chaque matin (seul le JSON change, pas la page) et garder un découplage propre entre présentation et contenu. Le HTML est servi tel quel par Pages, le JSON est ré-écrit par le cron.
