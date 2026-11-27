"use client";

export default function ServiceOutage({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">
      {message}
    </div>
  );
}
