"use client";

export default function PermissionDenied({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="status">
      {message}
    </div>
  );
}
