from sqlite3 import Connection

from travelmind.research.social.schemas import Platform, keyword_key


def evidence_page(
    db: Connection,
    platform: Platform,
    keywords: list[str],
    *,
    destination: str | None,
    limit: int,
    offset: int = 0,
    comments_only: bool = False,
) -> tuple[int, list[str]]:
    primary = keyword_key(keywords[0])
    exact = keyword_key(keywords[-1])
    place = keyword_key(destination or "")
    city = place.removesuffix("市") if len(place) > 2 else place
    select = """
        WITH matches AS (
          SELECT post_id, keyword, record, collected,
            CASE WHEN keyword=:primary THEN 2 WHEN keyword=:exact THEN 1 ELSE 0 END AS priority
          FROM evidence
          WHERE platform=:platform
          AND (NOT :comments_only OR json_array_length(record, '$.comments') > 0)
          AND (
            keyword IN (:primary, :exact) OR (:city<>'' AND (
              keyword=:city OR instr(keyword, :city || ' ')=1 OR instr(keyword, :place || ' ')=1
            ))
          )
        ), candidates AS (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY post_id ORDER BY priority DESC, collected DESC, keyword
          ) AS rank FROM matches
        )
    """
    params = {
        "platform": platform, "primary": primary, "exact": exact, "place": place, "city": city,
        "limit": limit, "offset": offset, "comments_only": comments_only,
    }
    count = db.execute(select + "SELECT COUNT(*) FROM candidates WHERE rank=1", params)
    rows = db.execute(
        select + """SELECT record FROM candidates WHERE rank=1
                    ORDER BY priority DESC, collected DESC, post_id LIMIT :limit OFFSET :offset""",
        params,
    )
    return count.fetchone()[0], [entry for (entry,) in rows]
