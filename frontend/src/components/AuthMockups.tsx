// Decorative product previews for the auth hero (laptop dashboard + phone).
// Everything is sized in `em` and the wrapper sets font-size from the viewport
// HEIGHT, so the whole composition scales down together and never forces the
// page to scroll. Purely illustrative: the numbers are fictional-range sample
// data and the hero carries a "previews are illustrative" disclaimer.
// aria-hidden - it is decoration, not content.

const NAV = ["Overview", "My Numbers", "Calls", "Routing", "AI Receptionist", "Billing"];

function Flag({ code, bg }: { code: string; bg: string }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-[0.25em] text-white font-bold"
      style={{ width: "1.9em", height: "1.3em", fontSize: "0.7em", background: bg }}
    >
      {code}
    </span>
  );
}

export default function AuthMockups() {
  return (
    <div
      aria-hidden="true"
      className="relative select-none pointer-events-none"
      style={{ fontSize: "clamp(3.5px, 0.92vh, 9px)", width: "60em", height: "31em" }}
    >
      {/* Laptop */}
      <div className="absolute" style={{ left: "15em", bottom: 0, width: "45em" }}>
        <div
          className="bg-slate-950 shadow-2xl"
          style={{ borderRadius: "1em 1em 0 0", padding: "0.7em 0.7em 0" }}
        >
          <div className="flex bg-white overflow-hidden" style={{ height: "25em", borderRadius: "0.4em 0.4em 0 0" }}>
            <div className="bg-slate-950 text-white shrink-0" style={{ width: "10.5em", padding: "1.4em 0.9em" }}>
              <div className="font-bold" style={{ fontSize: "1.15em", marginBottom: "1.6em" }}>
                ZOIKO<span className="font-normal">LOCAL</span>
              </div>
              {NAV.map((n, i) => (
                <div
                  key={n}
                  className={`rounded ${i === 0 ? "bg-white text-slate-900 font-semibold" : "text-slate-300"}`}
                  style={{ fontSize: "0.95em", padding: "0.55em 0.7em", marginBottom: "0.35em" }}
                >
                  {n}
                </div>
              ))}
            </div>

            <div className="flex-1 bg-slate-50" style={{ padding: "1.5em 1.6em" }}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-bold text-slate-900" style={{ fontSize: "1.55em" }}>
                    Your global numbers
                  </div>
                  <div className="text-slate-500" style={{ fontSize: "0.8em", marginTop: "0.3em" }}>
                    One place to manage your business communications
                  </div>
                </div>
                <div
                  className="bg-slate-800 text-white font-semibold whitespace-nowrap"
                  style={{ fontSize: "0.8em", padding: "0.6em 1em", borderRadius: "0.4em" }}
                >
                  + Add a number
                </div>
              </div>

              <div className="grid grid-cols-2" style={{ gap: "0.9em", marginTop: "1.4em" }}>
                {[
                  { c: "US", bg: "#3c5a99", n: "+1 (212) 555-0142", l: "United States" },
                  { c: "UK", bg: "#c8102e", n: "+44 20 7946 0426", l: "United Kingdom" },
                ].map((x) => (
                  <div key={x.c} className="bg-white border border-slate-200" style={{ borderRadius: "0.6em", padding: "0.9em" }}>
                    <div className="flex items-center gap-[0.5em] font-semibold text-slate-900" style={{ fontSize: "0.9em" }}>
                      <Flag code={x.c} bg={x.bg} /> {x.l}
                    </div>
                    <div className="font-bold text-slate-900" style={{ fontSize: "1em", marginTop: "0.5em" }}>
                      {x.n}
                    </div>
                    <div className="text-emerald-600" style={{ fontSize: "0.75em", marginTop: "0.3em" }}>
                      &bull; Active
                    </div>
                  </div>
                ))}
              </div>

              <div className="font-semibold text-slate-900" style={{ fontSize: "0.95em", marginTop: "1.4em" }}>
                Quick actions
              </div>
              <div className="grid grid-cols-3" style={{ gap: "0.8em", marginTop: "0.6em" }}>
                {["Make a call", "Call routing", "AI Receptionist"].map((q) => (
                  <div
                    key={q}
                    className="bg-white border border-slate-200 text-center text-slate-700 font-medium"
                    style={{ fontSize: "0.8em", padding: "1.1em 0.4em", borderRadius: "0.5em" }}
                  >
                    <div className="text-indigo-600" style={{ fontSize: "1.4em", lineHeight: 1 }}>
                      &#9679;
                    </div>
                    {q}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        <div className="bg-slate-300 mx-auto" style={{ height: "0.9em", width: "108%", marginLeft: "-4%", borderRadius: "0 0 1em 1em" }} />
      </div>

      {/* Phone */}
      <div
        className="absolute bg-slate-950 shadow-2xl"
        style={{
          left: 0,
          bottom: "0.5em",
          width: "15.5em",
          height: "29em",
          borderRadius: "2.2em",
          padding: "0.6em",
          transform: "rotate(-9deg)",
        }}
      >
        <div className="w-full h-full bg-white overflow-hidden" style={{ borderRadius: "1.7em", padding: "2em 1.2em" }}>
          <div className="font-bold text-slate-900" style={{ fontSize: "0.95em" }}>
            ZOIKO<span className="font-normal">LOCAL</span>
          </div>
          <div className="text-slate-500" style={{ fontSize: "0.7em", marginTop: "1.2em" }}>
            Welcome back
          </div>
          <div className="font-bold text-slate-900" style={{ fontSize: "1.3em" }}>
            My numbers
          </div>
          {[
            { c: "US", bg: "#3c5a99", t: "US Number", n: "+1 (212) 555-0142" },
            { c: "UK", bg: "#c8102e", t: "UK Number", n: "+44 20 7946 0426" },
          ].map((x) => (
            <div key={x.c} className="border border-slate-200 bg-slate-50" style={{ borderRadius: "0.8em", padding: "0.8em", marginTop: "0.8em" }}>
              <div className="flex items-center gap-[0.5em] font-semibold text-slate-900" style={{ fontSize: "0.85em" }}>
                <Flag code={x.c} bg={x.bg} /> {x.t}
              </div>
              <div className="text-slate-500" style={{ fontSize: "0.65em", marginTop: "0.3em" }}>
                {x.n}
              </div>
            </div>
          ))}
          <div
            className="bg-indigo-600 text-white text-center font-semibold"
            style={{ fontSize: "0.8em", padding: "0.8em", borderRadius: "0.8em", marginTop: "1.6em" }}
          >
            Manage numbers
          </div>
        </div>
      </div>
    </div>
  );
}
