import { useState } from 'react';
import ExpenseForm from '../components/ExpenseForm';
import ExpenseList from '../components/ExpenseList';

function Expenses() {
  const [expenses, setExpenses] = useState([]);
  
  return (
    <div>
      <h1>Expenses</h1>
      <ExpenseForm onSubmit={(expense) => setExpenses([...expenses, expense])} />
      <ExpenseList expenses={expenses} />
    </div>
  );
}

export default Expenses;