import os
import time
from itertools import combinations
from collections import defaultdict

import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
from dotenv import load_dotenv

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


# ---------------------------------------------------------
# 1. Settings
# ---------------------------------------------------------

INPUT_NODES_PATH = "outputs/tables/nodes_clean.csv"
INPUT_GENRE_PAIRS_PATH = "outputs/tables/artist_pairs_genre_similarity.csv"

OUTPUT_TABLES_DIR = "outputs/tables"
OUTPUT_FIGURES_DIR = "outputs/figures"
OUTPUT_NETWORKS_DIR = "outputs/networks"

os.makedirs(OUTPUT_TABLES_DIR, exist_ok=True)
os.makedirs(OUTPUT_FIGURES_DIR, exist_ok=True)
os.makedirs(OUTPUT_NETWORKS_DIR, exist_ok=True)

# To keep the pilot manageable.
MAX_ALBUMS_PER_ARTIST = 200
SLEEP_SECONDS = 0.08


# ---------------------------------------------------------
# 2. Load Spotify credentials
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
# 3. Load cleaned artist nodes
# ---------------------------------------------------------

nodes = pd.read_csv(INPUT_NODES_PATH)

nodes = nodes.dropna(subset=["spotify_id"]).copy()

id_to_query_name = dict(zip(nodes["spotify_id"], nodes["query_name"]))
query_name_to_id = dict(zip(nodes["query_name"], nodes["spotify_id"]))

sample_artist_ids = set(nodes["spotify_id"])
sample_artist_names = set(nodes["query_name"])

print(f"Loaded {len(nodes)} artists for collaboration search.")


# ---------------------------------------------------------
# 4. Helper functions
# ---------------------------------------------------------

def get_artist_albums(artist_id, max_albums=200):
    """
    Collects albums/singles/appearances/compilations for one artist.
    Returns simplified album objects.
    """
    albums = []
    seen_album_ids = set()
    offset = 0
    limit = 50

    while True:
        result = sp.artist_albums(
            artist_id,
            include_groups="album,single,appears_on,compilation",
            limit=limit,
            offset=offset
        )

        items = result.get("items", [])

        if not items:
            break

        for album in items:
            album_id = album.get("id")

            if album_id not in seen_album_ids:
                seen_album_ids.add(album_id)
                albums.append(album)

            if len(albums) >= max_albums:
                return albums

        offset += limit

        if result.get("next") is None:
            break

        time.sleep(SLEEP_SECONDS)

    return albums


def get_album_tracks(album):
    """
    Collects tracks from one Spotify album/release.
    """
    album_id = album.get("id")
    tracks = []
    offset = 0
    limit = 50

    while True:
        result = sp.album_tracks(album_id, limit=limit, offset=offset)
        items = result.get("items", [])

        if not items:
            break

        for track in items:
            tracks.append({
                "track_id": track.get("id"),
                "track_name": track.get("name"),
                "album_id": album_id,
                "album_name": album.get("name"),
                "album_type": album.get("album_type"),
                "release_date": album.get("release_date"),
                "track_artist_ids": [a.get("id") for a in track.get("artists", [])],
                "track_artist_names": [a.get("name") for a in track.get("artists", [])],
                "track_url": track.get("external_urls", {}).get("spotify")
            })

        offset += limit

        if result.get("next") is None:
            break

        time.sleep(SLEEP_SECONDS)

    return tracks


def canonical_pair(a, b):
    return tuple(sorted([a, b]))


# ---------------------------------------------------------
# 5. Collect track-level evidence
# ---------------------------------------------------------

all_track_records = []
seen_tracks = set()

for _, row in nodes.iterrows():
    artist_name = row["query_name"]
    artist_id = row["spotify_id"]

    print(f"\nCollecting releases for: {artist_name}")

    try:
        albums = get_artist_albums(
            artist_id,
            max_albums=MAX_ALBUMS_PER_ARTIST
        )
    except Exception as e:
        print(f"Could not collect albums for {artist_name}: {e}")
        continue

    print(f"Found {len(albums)} releases/appearances.")

    for album in albums:
        try:
            tracks = get_album_tracks(album)
        except Exception as e:
            print(f"Could not collect tracks for album {album.get('name')}: {e}")
            continue

        for track in tracks:
            track_id = track["track_id"]

            if track_id in seen_tracks:
                continue

            seen_tracks.add(track_id)

            present_sample_artist_ids = [
                aid for aid in track["track_artist_ids"]
                if aid in sample_artist_ids
            ]

            present_sample_artist_names = [
                id_to_query_name[aid]
                for aid in present_sample_artist_ids
            ]

            all_track_records.append({
                "track_id": track["track_id"],
                "track_name": track["track_name"],
                "album_id": track["album_id"],
                "album_name": track["album_name"],
                "album_type": track["album_type"],
                "release_date": track["release_date"],
                "all_track_artist_names": "; ".join(track["track_artist_names"]),
                "sample_artist_names": "; ".join(present_sample_artist_names),
                "n_sample_artists_on_track": len(present_sample_artist_names),
                "track_url": track["track_url"]
            })

        time.sleep(SLEEP_SECONDS)


tracks_df = pd.DataFrame(all_track_records)

tracks_df.to_csv(
    f"{OUTPUT_TABLES_DIR}/spotify_tracks_checked_for_collaborations.csv",
    index=False
)

collab_tracks = tracks_df[
    tracks_df["n_sample_artists_on_track"] >= 2
].copy()

collab_tracks.to_csv(
    f"{OUTPUT_TABLES_DIR}/collaboration_tracks_evidence.csv",
    index=False
)

print(f"\nTotal unique tracks checked: {len(tracks_df)}")
print(f"Tracks with at least two sample artists: {len(collab_tracks)}")


# ---------------------------------------------------------
# 6. Build collaboration edge list
# ---------------------------------------------------------

pair_to_tracks = defaultdict(list)

for _, row in collab_tracks.iterrows():
    artists_on_track = [
        x.strip()
        for x in row["sample_artist_names"].split(";")
        if x.strip()
    ]

    for a, b in combinations(sorted(artists_on_track), 2):
        pair = canonical_pair(a, b)

        pair_to_tracks[pair].append({
            "track_name": row["track_name"],
            "album_name": row["album_name"],
            "release_date": row["release_date"],
            "track_url": row["track_url"]
        })


edge_records = []

for (a, b), evidence_list in pair_to_tracks.items():
    track_names = [e["track_name"] for e in evidence_list]
    album_names = [e["album_name"] for e in evidence_list]
    release_dates = [e["release_date"] for e in evidence_list]
    urls = [e["track_url"] for e in evidence_list]

    edge_records.append({
        "source": a,
        "target": b,
        "collaboration": 1,
        "collaboration_weight": len(evidence_list),
        "evidence_tracks": "; ".join(track_names[:10]),
        "evidence_albums": "; ".join(album_names[:10]),
        "evidence_release_dates": "; ".join([str(x) for x in release_dates[:10]]),
        "evidence_urls": "; ".join([str(x) for x in urls[:10]])
    })


collab_edges = pd.DataFrame(edge_records)

collab_edges = collab_edges.sort_values(
    by="collaboration_weight",
    ascending=False
)

collab_edges.to_csv(
    f"{OUTPUT_TABLES_DIR}/collaboration_edges.csv",
    index=False
)

print(f"\nCollaboration edges found: {len(collab_edges)}")

if len(collab_edges) > 0:
    print(collab_edges.head(20)[
        ["source", "target", "collaboration_weight", "evidence_tracks"]
    ])
else:
    print("No collaboration edges were found among the sample artists.")


# ---------------------------------------------------------
# 7. Build collaboration network
# ---------------------------------------------------------

G = nx.Graph()

for _, row in nodes.iterrows():
    G.add_node(
        row["query_name"],
        spotify_id=row["spotify_id"],
        spotify_name=row["spotify_name"],
        popularity=int(row["popularity"]) if not pd.isna(row["popularity"]) else 0,
        followers=int(row["followers"]) if not pd.isna(row["followers"]) else 0,
        genres=row["genres"] if not pd.isna(row["genres"]) else ""
    )

for _, row in collab_edges.iterrows():
    G.add_edge(
        row["source"],
        row["target"],
        weight=int(row["collaboration_weight"]),
        evidence_tracks=row["evidence_tracks"]
    )


# ---------------------------------------------------------
# 8. Collaboration network metrics
# ---------------------------------------------------------

degree_centrality = nx.degree_centrality(G)
betweenness_centrality = nx.betweenness_centrality(G, weight=None)

if G.number_of_edges() > 0:
    communities = nx.algorithms.community.greedy_modularity_communities(
        G,
        weight="weight"
    )

    community_map = {}
    for community_id, community_nodes in enumerate(communities, start=1):
        for node in community_nodes:
            community_map[node] = community_id
else:
    community_map = {node: 1 for node in G.nodes()}


node_metrics = []

for node in G.nodes():
    node_metrics.append({
        "artist": node,
        "degree": G.degree(node),
        "degree_centrality": round(degree_centrality[node], 4),
        "betweenness_centrality": round(betweenness_centrality[node], 4),
        "community": community_map.get(node),
        "genres": G.nodes[node]["genres"],
        "popularity": G.nodes[node]["popularity"],
        "followers": G.nodes[node]["followers"]
    })

node_metrics = pd.DataFrame(node_metrics)

node_metrics = node_metrics.sort_values(
    by=["degree_centrality", "betweenness_centrality"],
    ascending=False
)

node_metrics.to_csv(
    f"{OUTPUT_TABLES_DIR}/collaboration_network_node_metrics.csv",
    index=False
)


network_summary = pd.DataFrame([{
    "n_nodes": G.number_of_nodes(),
    "n_edges": G.number_of_edges(),
    "density": round(nx.density(G), 4),
    "n_connected_components": nx.number_connected_components(G),
    "n_tracks_checked": len(tracks_df),
    "n_collaboration_tracks": len(collab_tracks)
}])

network_summary.to_csv(
    f"{OUTPUT_TABLES_DIR}/collaboration_network_summary.csv",
    index=False
)

print("\nCollaboration network summary:")
print(network_summary)

print("\nTop central artists in collaboration network:")
print(node_metrics.head(10)[[
    "artist",
    "degree",
    "degree_centrality",
    "betweenness_centrality",
    "community"
]])


# ---------------------------------------------------------
# 9. Compare collaboration network with genre similarity
# ---------------------------------------------------------

genre_pairs = pd.read_csv(INPUT_GENRE_PAIRS_PATH)

genre_pairs["pair_key"] = genre_pairs.apply(
    lambda row: "|||".join(sorted([row["source"], row["target"]])),
    axis=1
)

if len(collab_edges) > 0:
    collab_edges["pair_key"] = collab_edges.apply(
        lambda row: "|||".join(sorted([row["source"], row["target"]])),
        axis=1
    )

    comparison = genre_pairs.merge(
        collab_edges[[
            "pair_key",
            "collaboration",
            "collaboration_weight",
            "evidence_tracks"
        ]],
        on="pair_key",
        how="left"
    )

    comparison["collaboration"] = comparison["collaboration"].fillna(0).astype(int)
    comparison["collaboration_weight"] = comparison["collaboration_weight"].fillna(0).astype(int)
else:
    comparison = genre_pairs.copy()
    comparison["collaboration"] = 0
    comparison["collaboration_weight"] = 0
    comparison["evidence_tracks"] = ""


comparison.to_csv(
    f"{OUTPUT_TABLES_DIR}/network_comparison_pairs.csv",
    index=False
)

if len(comparison) > 0:
    mean_jaccard_collab = comparison.loc[
        comparison["collaboration"] == 1,
        "jaccard_similarity"
    ].mean()

    mean_jaccard_no_collab = comparison.loc[
        comparison["collaboration"] == 0,
        "jaccard_similarity"
    ].mean()

    comparison_summary = pd.DataFrame([{
        "n_artist_pairs": len(comparison),
        "n_collaboration_pairs": int(comparison["collaboration"].sum()),
        "mean_jaccard_for_collaboration_pairs": round(mean_jaccard_collab, 4) if pd.notna(mean_jaccard_collab) else None,
        "mean_jaccard_for_non_collaboration_pairs": round(mean_jaccard_no_collab, 4) if pd.notna(mean_jaccard_no_collab) else None
    }])
else:
    comparison_summary = pd.DataFrame([{
        "n_artist_pairs": 0,
        "n_collaboration_pairs": 0,
        "mean_jaccard_for_collaboration_pairs": None,
        "mean_jaccard_for_non_collaboration_pairs": None
    }])

comparison_summary.to_csv(
    f"{OUTPUT_TABLES_DIR}/network_comparison_summary.csv",
    index=False
)

print("\nNetwork comparison summary:")
print(comparison_summary)


# ---------------------------------------------------------
# 10. Save network for Gephi / future R analysis
# ---------------------------------------------------------

nx.write_graphml(
    G,
    f"{OUTPUT_NETWORKS_DIR}/collaboration_network.graphml"
)


# ---------------------------------------------------------
# 11. Simple visualization
# ---------------------------------------------------------

plt.figure(figsize=(14, 10))

pos = nx.spring_layout(G, seed=42, weight="weight")

node_sizes = [
    200 + G.nodes[node]["popularity"] * 10
    for node in G.nodes()
]

node_colors = [
    community_map.get(node, 0)
    for node in G.nodes()
]

edge_widths = [
    1 + G[u][v]["weight"]
    for u, v in G.edges()
]

nx.draw_networkx_nodes(
    G,
    pos,
    node_size=node_sizes,
    node_color=node_colors,
    alpha=0.85
)

nx.draw_networkx_edges(
    G,
    pos,
    width=edge_widths,
    alpha=0.45
)

nx.draw_networkx_labels(
    G,
    pos,
    font_size=8
)

plt.title(
    "Spotify Collaboration Network among Selected Artists",
    fontsize=14
)

plt.axis("off")
plt.tight_layout()

plt.savefig(
    f"{OUTPUT_FIGURES_DIR}/collaboration_network.png",
    dpi=300
)

plt.show()