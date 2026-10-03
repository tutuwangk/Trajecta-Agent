import { MapPin } from "lucide-react";

export function AmapLinkButton({ href, label = "打开地图" }: { href?: string; label?: string }) {
  if (!href) {
    return <span className="text-xs text-muted">暂无地图</span>;
  }
  return (
    <a
      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1.5 text-xs font-medium text-ink transition hover:border-ink/30"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      <MapPin size={14} />
      {label}
    </a>
  );
}
