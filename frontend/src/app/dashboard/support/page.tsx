export default function SupportPage() {
  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Support</h2>
        <p className="text-sm text-slate-500">Get help with your Zoiko Local account.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Contact us</h3>
        <p className="text-sm text-slate-600">
          For account issues, billing questions, or anything else, reach out and we&apos;ll get back to you.
        </p>
        <a
          href="mailto:support@zoikolocal.com"
          className="inline-block text-sm font-medium text-indigo-600 hover:text-indigo-800"
        >
          support@zoikolocal.com
        </a>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
        <h3 className="font-semibold text-slate-900">Before you reach out</h3>
        <ul className="text-sm text-slate-600 space-y-2 list-disc list-inside">
          <li>
            Check the{" "}
            <a href="/dashboard/business" className="text-indigo-600 hover:text-indigo-800 font-medium">
              Business
            </a>{" "}
            page for platform service status — some issues resolve themselves once a connected provider recovers.
          </li>
          <li>
            Check{" "}
            <a href="/dashboard/security" className="text-indigo-600 hover:text-indigo-800 font-medium">
              Security
            </a>{" "}
            for a record of recent account activity, if something looks unfamiliar.
          </li>
          <li>Include your account email and, if relevant, the affected phone number when you contact us.</li>
        </ul>
      </div>
    </div>
  );
}
