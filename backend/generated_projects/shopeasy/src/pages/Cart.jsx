import React from 'react';
import CartItem from '../components/CartItem';

function Cart() {
  return (
    <div>
      <h1>Your Cart</h1>
      <div className="cart-items">
        <CartItem />
      </div>
      <button>Proceed to Checkout</button>
    </div>
  );
}

export default Cart;