import Link from "next/link";
import { ArrowRight, Compass, FlaskConical, UsersRound } from "lucide-react";
import { ResearchForm } from "@/components/research-form";
import { ReportArchive } from "@/components/report-archive";
import { demo } from "@/lib/demo";

export default function Home() {
  return (
    <main className="shell home">
      <section className="hero" aria-labelledby="home-title">
        <div className="hero-content">
          <p className="hero-label"><Compass size={17} aria-hidden /> 多智能体旅游调研平台</p>
          <h1 id="home-title">出发之前，<br className="mobile-break" />先深入了解。</h1>
          <p className="hero-description">从地方文化到出行路线，让多位专家协作调研，<br className="desktop-break" />为你的旅行提供一份可追溯的报告。</p>
          <Link className="hero-link" href={`/workbench/${demo.taskId}`}><FlaskConical size={16} aria-hidden /> 查看示例工作台 <ArrowRight size={16} aria-hidden /></Link>
        </div>
      </section>
      <ResearchForm />
      <div className="home-bottom">
        <section aria-labelledby="recent-title">
          <div className="section-heading"><h2 id="recent-title">最近报告</h2><Link className="text-link" href="/reports">报告中心 <ArrowRight size={16} aria-hidden /></Link></div>
          <ReportArchive compact />
        </section>
        <section className="guild-callout" aria-labelledby="team-title">
          <span className="icon-tile"><UsersRound size={22} aria-hidden /></span>
          <div><h2 id="team-title">专家公会</h2><p>查看调研总监、专业研究员与报告审核员的分工。</p>
            <Link className="text-link" href="/guild">浏览专家档案 <ArrowRight size={16} aria-hidden /></Link>
          </div>
        </section>
      </div>
    </main>
  );
}
