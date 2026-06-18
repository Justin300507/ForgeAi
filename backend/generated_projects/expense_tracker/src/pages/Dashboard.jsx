import BudgetProgress from '../components/BudgetProgress';

function Dashboard() {
  return (
    <div>
      <h1>Dashboard</h1>
      <BudgetProgress category="Food" spent={350} limit={500} />
    </div>
  );
}

export default Dashboard;