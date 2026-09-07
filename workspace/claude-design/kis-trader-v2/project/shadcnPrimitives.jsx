/* global React, window */
// Small, dependency-free primitives inspired by shadcn/ui's owned-source model.
// They intentionally preserve this dashboard's static React/Babel runtime.

function ktCn(...values) {
  return values.filter(Boolean).join(" ");
}

function UiCard({ as: Component = "section", className = "", children, ...props }) {
  return <Component className={ktCn("kt-ui-card", className)} {...props}>{children}</Component>;
}

function UiCardHeader({ className = "", children, ...props }) {
  return <div className={ktCn("kt-ui-card-header", className)} {...props}>{children}</div>;
}

function UiCardTitle({ as: Component = "h2", className = "", children, ...props }) {
  return <Component className={ktCn("kt-ui-card-title", className)} {...props}>{children}</Component>;
}

function UiCardDescription({ className = "", children, ...props }) {
  return <p className={ktCn("kt-ui-card-description", className)} {...props}>{children}</p>;
}

function UiCardContent({ className = "", children, ...props }) {
  return <div className={ktCn("kt-ui-card-content", className)} {...props}>{children}</div>;
}

function UiBadge({ tone = "neutral", className = "", children, ...props }) {
  return <span className={ktCn("kt-ui-badge", `is-${tone}`, className)} {...props}>{children}</span>;
}

function UiButton({
  variant = "ghost",
  size = "default",
  className = "",
  type = "button",
  children,
  "aria-label": ariaLabel,
  ...props
}) {
  return (
    <button
      type={type}
      className={ktCn("kt-ui-button", `is-${variant}`, `is-${size}`, className)}
      aria-label={ariaLabel}
      {...props}
    >
      {children}
    </button>
  );
}

function UiSeparator({ orientation = "horizontal", className = "", ...props }) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={ktCn("kt-ui-separator", `is-${orientation}`, className)}
      {...props}
    />
  );
}

function UiProgress({ value = 0, max = 100, label, className = "" }) {
  const safeMax = Math.max(1, Number(max) || 1);
  const safeValue = Math.min(safeMax, Math.max(0, Number(value) || 0));
  const percent = (safeValue / safeMax) * 100;
  return (
    <div
      className={ktCn("kt-ui-progress", className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin="0"
      aria-valuemax={safeMax}
      aria-valuenow={safeValue}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  );
}

window.KtPrimitives = {
  UiCard,
  UiCardHeader,
  UiCardTitle,
  UiCardDescription,
  UiCardContent,
  UiBadge,
  UiButton,
  UiSeparator,
  UiProgress,
};
