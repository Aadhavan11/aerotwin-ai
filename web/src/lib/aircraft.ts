/** Published type data used for the viewer caption only. The 3D geometry is the third-party model in public/models/b737-800 (docs/ASSETS.md). */
export const profiles = {
  a320: { label: "Airbus A320-200", length: 37.57, span: 35.8 },
  b738: { label: "Boeing 737-800", length: 39.5, span: 35.8 },
};
export function profileFor(type: string) {
  return /B738|737/i.test(type) ? profiles.b738 : /A320/i.test(type) ? profiles.a320 : null;
}
