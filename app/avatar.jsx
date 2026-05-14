function Avatar({ size = "lg" }) {
  const cls = size === "xs" ? "avatar xs" : size === "sm" ? "avatar sm" : "avatar";
  const base = window.__CHLOE_API_BASE__ || '';
  return (
    <div className={cls}>
      <img src={base + "/app/Chloe_image.png"} alt="Chloe" className="avatar-img" />
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
