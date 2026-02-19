module reference_wave_tb;
  logic       clk;
  logic       rst_n;
  logic       clear;
  logic       en;
  logic [3:0] din;
  logic [1:0] mode;
  logic [5:0] accum;
  logic [3:0] dout;
  logic       valid;
  logic       parity;

  sample_dut u_dut (
    .clk(clk),
    .rst_n(rst_n),
    .clear(clear),
    .en(en),
    .din(din),
    .mode(mode),
    .accum(accum),
    .dout(dout),
    .valid(valid),
    .parity(parity)
  );

  task automatic step_cycle;
    begin
      #5 clk = 1'b1;
      #5 clk = 1'b0;
    end
  endtask

  initial begin
    $dumpfile("test/out/reference.vcd");
    $dumpvars(0, reference_wave_tb);

    clk = 1'b0;
    rst_n = 1'b0;
    clear = 1'b0;
    en = 1'b0;
    din = 4'd0;
    mode = 2'b00;

    // Cycle 0: keep reset asserted.
    step_cycle();

    // Cycle 1.
    rst_n = 1'b1;
    clear = 1'b0;
    en = 1'b1;
    din = 4'd3;
    mode = 2'b00;
    step_cycle();

    // Cycle 2.
    step_cycle();

    // Cycle 3.
    en = 1'b0;
    step_cycle();

    // Cycle 4.
    step_cycle();

    // Cycle 5.
    step_cycle();

    // Cycle 6.
    clear = 1'b1;
    step_cycle();

    // Cycle 7.
    clear = 1'b0;
    en = 1'b1;
    din = 4'd5;
    mode = 2'b01;
    step_cycle();

    // Cycle 8.
    step_cycle();

    // Cycle 9.
    din = 4'd1;
    mode = 2'b10;
    step_cycle();

    // Cycle 10.
    step_cycle();

    // Cycle 11.
    din = 4'd2;
    mode = 2'b11;
    step_cycle();

    // Cycle 12.
    step_cycle();

    // Cycle 13.
    en = 1'b0;
    step_cycle();

    // Cycle 14.
    step_cycle();

    // Cycle 15.
    step_cycle();

    #10;
    $finish;
  end
endmodule
