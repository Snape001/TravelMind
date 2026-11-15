import { ResearchForm } from "@/components/research-form";

export default function Home() {
  return (
    <main className="destination-home">
      <section className="destination-hero" aria-labelledby="home-title">
        <div className="destination-content">
          <h1 id="home-title"><span>山海寻踪，</span><span>知行有据</span></h1>
          <ResearchForm />
        </div>
      </section>
    </main>
  );
}
