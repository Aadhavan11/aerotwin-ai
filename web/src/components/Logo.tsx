/** The AeroTwin mark: an airplane outline with its twin offset beside it (white and one blue accent). Transparent, so it sits on any dark surface. */
export default function Logo({ size = 38 }: { size?: number }) {
  return <img className="brand-logo" src={`${import.meta.env.BASE_URL}logo-mark.svg`} width={size} height={size} alt="AeroTwin" draggable={false} />;
}
