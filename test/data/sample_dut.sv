module sample_dut (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       clear,
  input  logic       en,
  input  logic [3:0] din,
  input  logic [1:0] mode,
  output logic [5:0] accum,
  output logic [3:0] dout,
  output logic       valid,
  output logic       parity
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accum <= 6'd0;
      dout <= 4'd0;
      valid <= 1'b0;
    end else if (clear) begin
      accum <= 6'd0;
      dout <= 4'd0;
      valid <= 1'b0;
    end else begin
      valid <= en;
      if (en) begin
        unique case (mode)
          2'b00: begin
            accum <= accum + {2'b00, din};
            dout <= din;
          end
          2'b01: begin
            accum <= accum ^ {2'b00, din};
            dout <= din ^ accum[3:0];
          end
          2'b10: begin
            accum <= {accum[4:0], din[0]};
            dout <= {dout[2:0], din[0]};
          end
          default: begin
            accum <= accum;
            dout <= ~din;
          end
        endcase
      end
    end
  end

  always_comb begin
    parity = ^accum ^ ^dout ^ valid;
  end
endmodule
