import os
import json
import pandas as pd
from difflib import SequenceMatcher
from dotenv import load_dotenv

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


# ---------------------------------------------------------
# 1. Load Spotify credentials
# ---------------------------------------------------------

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("Spotify credentials are missing. Check your .env file.")


sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )
)


# ---------------------------------------------------------
# 2. Artist list for the pilot study
# ---------------------------------------------------------

artist_names = [
    "Jane Remover",
    "ear",
    "Mk.gee",
    "ML Buch",
    "Chanel Beads",
    "Sophia Stel",
    "Bassvictim",
    "The Hellp",
    "the sound chalk makes",
    "Lucy Bedroque",
    "Horse Vision",
    "Mother Soki",
    "bar italia",
    "daine",
    "james K",
    "underscores",
    "Elias Rønnenfelt",
    "Bladee",
    "mark william lewis",
    "Nourished by Time",
    "Alex G",
    "Oklou",
    "Dean Blunt",
    "King Krule",
    "A. G. Cook",
    "Nation",
    "The Dare",
    "Ecco2k",
    "BABii",
    "2hollis"
    ""
]


# ---------------------------------------------------------
# 3. Helper function for name similarity
# ---------------------------------------------------------

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------------------------------------------------
# List for manually added artists

manual_artist_ids = {
    "ear": "3bABCGLkFvjnNIKHvPVHDG",
    "Nation": "03D2b6ATNCne8B3D251ncQ"
}

# ---------------------------------------------------------

# ---------------------------------------------------------
# 4. Search artists on Spotify
# ---------------------------------------------------------

records = []

for query_name in artist_names:
    print(f"Processing: {query_name}")

    # Manual verified Spotify ID for ambiguous artists
    if query_name in manual_artist_ids:
        artist = sp.artist(manual_artist_ids[query_name])

        records.append({
            "query_name": query_name,
            "spotify_id": artist.get("id"),
            "spotify_name": artist.get("name"),
            "match_score": 1.0,
            "genres": artist.get("genres", []),
            "n_genres": len(artist.get("genres", [])),
            "popularity": artist.get("popularity"),
            "followers": artist.get("followers", {}).get("total"),
            "spotify_url": artist.get("external_urls", {}).get("spotify"),
            "data_quality_note": "manual_verified"
        })

        continue

    # Automatic search for all other artists
    result = sp.search(q=query_name, type="artist", limit=5)
    items = result.get("artists", {}).get("items", [])

    if not items:
        records.append({
            "query_name": query_name,
            "spotify_id": None,
            "spotify_name": None,
            "match_score": None,
            "genres": [],
            "n_genres": 0,
            "popularity": None,
            "followers": None,
            "spotify_url": None,
            "data_quality_note": "not_found"
        })
        continue

    best = max(items, key=lambda x: similarity(query_name, x["name"]))
    match_score = similarity(query_name, best["name"])

    if match_score >= 0.95:
        note = "clear_match"
    elif match_score >= 0.75:
        note = "possible_match_check_manually"
    else:
        note = "ambiguous_check_manually"

    records.append({
        "query_name": query_name,
        "spotify_id": best.get("id"),
        "spotify_name": best.get("name"),
        "match_score": round(match_score, 3),
        "genres": best.get("genres", []),
        "n_genres": len(best.get("genres", [])),
        "popularity": best.get("popularity"),
        "followers": best.get("followers", {}).get("total"),
        "spotify_url": best.get("external_urls", {}).get("spotify"),
        "data_quality_note": note
    })

artists_df = pd.DataFrame(records)


# ---------------------------------------------------------
# 5. Save raw artist table
# ---------------------------------------------------------

os.makedirs("data/raw", exist_ok=True)

artists_df.to_csv("data/raw/artists_raw.csv", index=False)

# Save a readable version where genres are stored as a string
artists_readable = artists_df.copy()
artists_readable["genres"] = artists_readable["genres"].apply(lambda x: "; ".join(x) if isinstance(x, list) else "")

artists_readable.to_csv("data/raw/artists_raw_readable.csv", index=False)


# ---------------------------------------------------------
# 6. Print quality summary
# ---------------------------------------------------------

print("\nData collection completed.")
print("\nMatch quality summary:")
print(artists_df["data_quality_note"].value_counts(dropna=False))

print("\nArtists requiring manual check:")
print(
    artists_readable[
        artists_readable["data_quality_note"] != "clear_match"
    ][
        ["query_name", "spotify_name", "match_score", "genres", "spotify_url", "data_quality_note"]
    ]
)