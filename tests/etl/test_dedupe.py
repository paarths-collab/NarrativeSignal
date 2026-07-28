import pandas as pd

from etl.transforms.dedupe import assign_clusters, build_clusters_table


def test_reproduces_committed_artifact(golden_clean_posts, golden_clusters):
    got = build_clusters_table(golden_clean_posts)
    exp = golden_clusters
    assert len(got) == 8567
    assert got.duplicate_cluster_id.notna().sum() == 285
    assert got.duplicate_cluster_id.nunique() == 131
    pd.testing.assert_frame_equal(
        got.sort_values("id").reset_index(drop=True),
        exp.sort_values("id").reset_index(drop=True),
        check_dtype=False,
    )


def test_single_author_repeats_are_not_clusters(golden_clean_posts):
    """The discriminator: one person crossposting themselves is not spread."""
    clustered = assign_clusters(golden_clean_posts)
    per_title = clustered.groupby("title").author.nunique()
    assert (per_title >= 2).all()


def test_cluster_ids_are_alphabetical_by_title(golden_clean_posts):
    clustered = assign_clusters(golden_clean_posts)
    mapping = clustered.groupby("title").duplicate_cluster_id.first()
    assert list(mapping.sort_index().values) == sorted(mapping.values)


def test_clustering_precedes_deleted_filter(golden_clusters):
    """Cluster 83 is left a singleton because its only sibling was [deleted]."""
    sizes = golden_clusters.groupby("duplicate_cluster_id").size()
    assert (sizes == 1).sum() == 1
