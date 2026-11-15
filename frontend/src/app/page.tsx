import { ResearchForm } from "@/components/research-form";

export default function Home() {
  return (
    <main className="shell destination-home">
      <div className="destination-intro">
        <h1>想调研哪里？</h1>
        <ResearchForm />
      </div>
    </main>
  );
}
