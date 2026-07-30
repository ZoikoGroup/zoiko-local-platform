export const COUNTRIES = [
  { code: "US", name: "United States", dial: "+1" },
  { code: "GB", name: "United Kingdom", dial: "+44" },
  { code: "CA", name: "Canada", dial: "+1" },
  { code: "NG", name: "Nigeria", dial: "+234" },
  { code: "ZA", name: "South Africa", dial: "+27" },
  { code: "GH", name: "Ghana", dial: "+233" },
  { code: "KE", name: "Kenya", dial: "+254" },
  { code: "MX", name: "Mexico", dial: "+52" },
];

function randomDigits(count: number): string {
  return Array.from({ length: count }, () => Math.floor(Math.random() * 10)).join("");
}

/** Generates realistic-looking but entirely fake sample numbers for the
 * given country - NOT real inventory. There's no telecom provider (Twilio)
 * connected yet, so this only demonstrates the UI/UX of the search flow. */
export function generateSampleNumbers(countryCode: string, count = 3): string[] {
  const country = COUNTRIES.find((c) => c.code === countryCode);
  const dial = country?.dial ?? "+1";

  return Array.from({ length: count }, () => {
    if (dial === "+1") {
      return `${dial} (${randomDigits(3)}) ${randomDigits(3)}-${randomDigits(4)}`;
    }
    return `${dial} ${randomDigits(3)} ${randomDigits(3)} ${randomDigits(3)}`;
  });
}
