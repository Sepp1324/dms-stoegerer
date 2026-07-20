// Teilbarer Deep-Link auf ein Dokument (nutzt die /dokument/:id-Route aus #7).
export function documentLink(id: number): string {
  return `${window.location.origin}/dokument/${id}`;
}
