/* Avatar — watercolour circle with serif monogram + small botanical leaf.
   No hand-drawn portrait — abstract & tasteful. */

function Avatar({ size = "lg", monogram = "C" }) {
  const cls = size === "xs" ? "avatar xs" : size === "sm" ? "avatar sm" : "avatar";
  return (
    <div className={cls}>
      <div className="mono-c">{monogram}</div>
      <svg className="leaf" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M3 13c0-5 4-9 9-9 3 0 6 1 9 3-1 7-5 12-12 12-3 0-5-1-6-3 1 1 3 2 6 2 5 0 9-3 10-9-2 0-4 1-6 2 1-1 2-3 2-5-3 1-6 3-8 5-2 2-4 5-4 8 0-1 0-2 0-6z"/>
      </svg>
    </div>
  );
}

/* Small initial-avatar for persons (1.5cm-style watercolour disc) */
function PersonAv({ name, tone = 1 }) {
  const initial = (name || "?").trim().slice(0, 1);
  return (
    <div className="av" data-tone={tone}>{initial}</div>
  );
}

window.Avatar = Avatar;
window.PersonAv = PersonAv;
