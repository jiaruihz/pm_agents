export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state-msg">{message}</div>
      {hint && <div className="empty-state-hint">{hint}</div>}
    </div>
  );
}
