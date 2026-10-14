/** Who is signed in; the app shell sets it. Work orders record it as the approver (the server records the signed-in person regardless). */
let actor = "";
export const setActor = (name: string) => {
  actor = name;
};
export const actorName = (): string => actor || "Demo user";
