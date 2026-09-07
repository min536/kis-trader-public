/* global React, window */
// Owned kis-trader primitives for calm, row-first information architecture.
// The structure follows common mobile list conventions without importing TDS assets.

function ktListCn(...values) {
  return values.filter(Boolean).join(" ");
}

function KtPageTop({
  eyebrow,
  title,
  description,
  badge,
  meta,
  className = "",
  titleId,
}) {
  return (
    <div className={ktListCn("kt-page-top", className)}>
      <div className="kt-page-top__meta">
        {badge}
        {meta && <span>{meta}</span>}
      </div>
      {eyebrow && <p className="kt-page-top__eyebrow">{eyebrow}</p>}
      <h2 className="kt-page-top__title" id={titleId}>{title}</h2>
      {description && <p className="kt-page-top__description">{description}</p>}
    </div>
  );
}

function KtSectionHeader({ title, description, action, titleId, className = "" }) {
  return (
    <header className={ktListCn("kt-section-header", className)}>
      <div className="kt-section-header__copy">
        <h2 id={titleId}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {action && <div className="kt-section-header__action">{action}</div>}
    </header>
  );
}

function KtAsset({ children, tone = "accent", size = "md", label, className = "" }) {
  return (
    <span
      className={ktListCn("kt-list-asset", `is-${tone}`, `is-${size}`, className)}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
    >
      {children}
    </span>
  );
}

function KtListGroup({ as: Component = "div", className = "", children, ...props }) {
  return <Component className={ktListCn("kt-list-group", className)} {...props}>{children}</Component>;
}

function KtListRow({
  leading,
  eyebrow,
  title,
  description,
  trailing,
  showArrow = false,
  onClick,
  className = "",
  "aria-label": ariaLabel,
  children,
  ...props
}) {
  const Component = onClick ? "button" : "div";
  return (
    <Component
      type={onClick ? "button" : undefined}
      className={ktListCn("kt-list-row", onClick && "is-interactive", className)}
      onClick={onClick}
      aria-label={ariaLabel}
      {...props}
    >
      {leading && <span className="kt-list-row__leading">{leading}</span>}
      <span className="kt-list-row__contents">
        {eyebrow && <span className="kt-list-row__eyebrow">{eyebrow}</span>}
        {title && <strong className="kt-list-row__title">{title}</strong>}
        {description && <span className="kt-list-row__description">{description}</span>}
        {children}
      </span>
      {(trailing || showArrow) && (
        <span className="kt-list-row__right">
          {trailing && <span className="kt-list-row__trailing">{trailing}</span>}
          {showArrow && <window.Icon name="chevron-right" size={16} />}
        </span>
      )}
    </Component>
  );
}

window.KtListPrimitives = {
  KtPageTop,
  KtSectionHeader,
  KtAsset,
  KtListGroup,
  KtListRow,
};
