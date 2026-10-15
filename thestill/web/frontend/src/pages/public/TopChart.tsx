// Spec #94 — the Apple Podcasts chart for one country. Rows open the show's
// thestill page when it has one, otherwise the show on Apple Podcasts.
import { Link, useSearchParams } from "react-router-dom";
import { Avatar, Skeleton } from "../../components/public/ui";
import { usePublicTopChart } from "../../hooks/usePublicApi";
import { ModuleError } from "./NotFound";

const regionName = (code: string) => {
  try {
    return (
      new Intl.DisplayNames(undefined, { type: "region" }).of(
        code.toUpperCase(),
      ) ?? code.toUpperCase()
    );
  } catch {
    return code.toUpperCase();
  }
};

export default function TopChart() {
  const [params, setParams] = useSearchParams();
  const region = params.get("region") ?? undefined;
  const category = params.get("category") ?? undefined;
  const { data, isLoading, isError, refetch } = usePublicTopChart(
    region,
    category,
  );

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "region") next.delete("category");
    setParams(next, { replace: true });
  };

  return (
    <div className="ts-container">
      <section className="ts-hero" style={{ flexDirection: "column", gap: 10 }}>
        <span className="ts-eyebrow">Updated daily</span>
        <h1 className="ts-page-title">Top podcasts</h1>
        <p className="ts-standfirst">
          The Apple Podcasts chart
          {data ? ` for ${regionName(data.region)}` : ""}. Shows with summaries
          open on thestill.
        </p>
        {data && (
          <div
            style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 8 }}
          >
            {data.available_regions.length > 0 && (
              <label className="ts-meta">
                <span className="ts-visually-hidden">Country</span>
                <select
                  className="ts-chip"
                  value={data.region}
                  onChange={(e) => update("region", e.target.value)}
                >
                  {data.available_regions.map((code) => (
                    <option key={code} value={code}>
                      {regionName(code)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {data.available_categories.length > 0 && (
              <label className="ts-meta">
                <span className="ts-visually-hidden">Category</span>
                <select
                  className="ts-chip"
                  value={category ?? ""}
                  onChange={(e) => update("category", e.target.value)}
                >
                  <option value="">All categories</option>
                  {data.available_categories.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}
      </section>
      <div style={{ maxWidth: 840, paddingTop: 16 }}>
        {isError && <ModuleError onRetry={() => refetch()} />}
        {isLoading &&
          Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="ts-eprow" aria-hidden="true">
              <Skeleton width="60%" height={18} />
            </div>
          ))}
        {data && data.top_podcasts.length === 0 && (
          <p className="ts-error-line">
            {category
              ? "No shows in this category on today's chart."
              : "The chart for this country hasn't been fetched yet. Check back tomorrow."}
          </p>
        )}
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {data?.top_podcasts.map((row) => {
            const content = (
              <>
                <span
                  className="ts-meta"
                  style={{
                    width: 32,
                    justifyContent: "flex-end",
                    fontSize: 15,
                    fontWeight: 600,
                    color: "var(--on-surface)",
                  }}
                >
                  {row.rank}
                </span>
                <Avatar
                  src={row.image_url}
                  name={row.name}
                  size={48}
                  shape="square"
                />
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span
                    className="ts-ep-head-title"
                    style={{ display: "block" }}
                  >
                    {row.name}
                  </span>
                  <span className="ts-meta">
                    {[row.artist, row.category].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <span
                  className="ts-link"
                  style={{ flex: "none", fontSize: 13 }}
                >
                  {row.podcast_slug
                    ? "Read summaries"
                    : row.apple_url
                      ? "Apple Podcasts ↗"
                      : ""}
                </span>
              </>
            );
            return (
              <li
                key={`${row.rank}-${row.name}`}
                className="ts-eprow"
                style={{ padding: "12px 0" }}
              >
                {row.podcast_slug ? (
                  <Link
                    to={`/podcasts/${row.podcast_slug}`}
                    className="ts-ep-head"
                  >
                    {content}
                  </Link>
                ) : row.apple_url ? (
                  <a
                    href={row.apple_url}
                    className="ts-ep-head"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {content}
                  </a>
                ) : (
                  <div className="ts-ep-head">{content}</div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
